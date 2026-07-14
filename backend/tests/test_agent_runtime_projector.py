"""Focused tests for checkpoint-authoritative Runtime projections."""

from collections import deque
from datetime import UTC, datetime
import uuid

from langgraph.types import StateSnapshot
import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.projector import (
    ProjectionCheckpointError,
    ProjectionHistoryGapError,
    ProjectionNotFoundError,
    RuntimeProjectionTrigger,
    RuntimeProjector,
)


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self.scalar = scalar
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.scalar

    def all(self):
        return list(self.rows)


class _FakeSession:
    def __init__(self, *results: _Result):
        self.results = deque(results)
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database execute")
        return self.results.popleft()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1

    async def commit(self):
        raise AssertionError("projector must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("projector must not roll back the caller transaction")


class _SnapshotSource:
    def __init__(self, snapshots: list[StateSnapshot]):
        self.snapshots = snapshots
        self.configs = []

    async def aget_state_history(self, config):
        self.configs.append(config)
        for snapshot in self.snapshots:
            yield snapshot


def test_projector_requires_exactly_one_checkpoint_source_strategy():
    source = _SnapshotSource([])

    with pytest.raises(ValueError, match="exactly one"):
        RuntimeProjector()
    with pytest.raises(ValueError, match="exactly one"):
        RuntimeProjector(source, state_source_resolver=lambda _run: source)

    run = _run()
    resolved = []
    projector = RuntimeProjector(
        state_source_resolver=lambda value: resolved.append(value) or source
    )

    assert projector._source_for_run(run) is source
    assert resolved == [run]


def _run(
    *,
    tenant_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    projected_checkpoint_id: str | None = None,
    projected_execution_status: str | None = None,
    lane_held: bool = False,
) -> AgentRun:
    resolved_tenant_id = tenant_id or uuid.uuid4()
    resolved_run_id = run_id or uuid.uuid4()
    return AgentRun(
        id=resolved_run_id,
        tenant_id=resolved_tenant_id,
        agent_id=uuid.uuid4(),
        source_type="chat",
        goal="Finish the requested work",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(resolved_run_id),
        graph_name="test_runtime",
        graph_version="v1",
        lane_held=lane_held,
        delivery_status="pending",
        projected_checkpoint_id=projected_checkpoint_id,
        projected_execution_status=projected_execution_status,
    )


def _snapshot(
    run: AgentRun,
    *,
    checkpoint_id: str,
    version: int,
    status: str,
    created_at: datetime,
    parent_checkpoint_id: str | None = None,
    lifecycle: dict | None = None,
    registry_tenant_id: uuid.UUID | None = None,
) -> StateSnapshot:
    lifecycle_values = {
        "status": status,
        "next_route": "terminal"
        if status in {"completed", "failed", "cancelled"}
        else "wait"
        if status.startswith("waiting_")
        else "model",
        **(lifecycle or {}),
    }
    config = {
        "configurable": {
            "thread_id": run.runtime_thread_id,
            "checkpoint_id": checkpoint_id,
        }
    }
    parent_config = (
        {
            "configurable": {
                "thread_id": run.runtime_thread_id,
                "checkpoint_id": parent_checkpoint_id,
            }
        }
        if parent_checkpoint_id
        else None
    )
    return StateSnapshot(
        values={
            "registry": {
                "tenant_id": str(registry_tenant_id or run.tenant_id),
                "run_id": str(run.id),
                "graph_name": run.graph_name,
                "graph_version": run.graph_version,
            },
            "lifecycle": lifecycle_values,
        },
        next=(),
        config=config,
        metadata={"step": version},
        created_at=created_at.isoformat(),
        parent_config=parent_config,
        tasks=(),
        interrupts=(),
    )


def _framework_initial_snapshot(
    run: AgentRun,
    *,
    checkpoint_id: str = "framework-initial",
) -> StateSnapshot:
    return StateSnapshot(
        values={},
        next=("__start__",),
        config={
            "configurable": {
                "thread_id": run.runtime_thread_id,
                "checkpoint_id": checkpoint_id,
            }
        },
        metadata={"step": -1},
        created_at=datetime(2026, 7, 13, 7, 59, tzinfo=UTC).isoformat(),
        parent_config=None,
        tasks=(),
        interrupts=(),
    )


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_terminal_projection_uses_checkpoint_and_never_releases_lane():
    tenant_id = uuid.uuid4()
    run = _run(
        tenant_id=tenant_id,
        projected_checkpoint_id="checkpoint-1",
        projected_execution_status="running",
        lane_held=True,
    )
    started_at = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    completed_at = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    projection_time = datetime(2026, 7, 13, 10, 1, tzinfo=UTC)
    run.projected_started_at = started_at
    source = _SnapshotSource(
        [
            _snapshot(
                run,
                checkpoint_id="checkpoint-2",
                version=2,
                status="completed",
                created_at=completed_at,
                parent_checkpoint_id="checkpoint-1",
                lifecycle={
                    "result_summary": {
                        "summary": "Finished",
                        "artifact_refs": [{"path": "workspace/result.md"}],
                    }
                },
            ),
            _snapshot(
                run,
                checkpoint_id="checkpoint-1",
                version=1,
                status="running",
                created_at=started_at,
            ),
        ]
    )
    db = _FakeSession(_Result(scalar=run), _Result(rows=[]))

    result = await RuntimeProjector(source, clock=lambda: projection_time).project_run(
        db,
        tenant_id=tenant_id,
        run_id=run.id,
    )

    assert result.applied_checkpoint_ids == ("checkpoint-2",)
    assert result.authoritative_status == "completed"
    assert result.authoritative_terminal is True
    assert run.projected_execution_status == "completed"
    assert run.projected_result_summary == "Finished"
    assert run.projected_checkpoint_id == "checkpoint-2"
    assert run.projected_started_at == started_at
    assert run.projected_completed_at == completed_at
    assert run.projection_updated_at == projection_time
    assert run.lane_held is True
    assert [event.event_type for event in db.added] == [
        "status_changed",
        "run_completed",
    ]
    for event in db.added:
        assert isinstance(event, AgentRunEvent)
        assert event.tenant_id == tenant_id
        assert event.run_id == run.id
        assert event.source_checkpoint_id == "checkpoint-2"
        assert event.payload["checkpoint_version"] == 2
    assert db.flush_count == 1
    run_sql = _sql(db.statements[0])
    assert f"agent_runs.tenant_id = '{tenant_id}'" in run_sql
    assert f"agent_runs.id = '{run.id}'" in run_sql
    assert "agent_runs.runtime_type = 'langgraph'" in run_sql
    assert "FOR UPDATE" in run_sql


@pytest.mark.asyncio
async def test_waiting_projection_skips_an_existing_idempotent_event():
    run = _run(
        projected_checkpoint_id="checkpoint-0",
        projected_execution_status="queued",
    )
    queued_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    waiting_at = datetime(2026, 7, 13, 8, 1, tzinfo=UTC)
    source = _SnapshotSource(
        [
            _snapshot(
                run,
                checkpoint_id="checkpoint-1",
                version=1,
                status="waiting_user",
                created_at=waiting_at,
                parent_checkpoint_id="checkpoint-0",
                lifecycle={
                    "waiting_request": {
                        "waiting_type": "user",
                        "reason": "Confirm the plan",
                        "correlation_id": "confirm-plan-1",
                    }
                },
            ),
            _snapshot(
                run,
                checkpoint_id="checkpoint-0",
                version=0,
                status="queued",
                created_at=queued_at,
            ),
        ]
    )
    db = _FakeSession(
        _Result(scalar=run),
        _Result(rows=[("checkpoint-1", "status_changed")]),
    )

    result = await RuntimeProjector(source).project_run(
        db,
        tenant_id=run.tenant_id,
        run_id=run.id,
    )

    assert result.added_event_types == ("waiting_started",)
    assert run.projected_execution_status == "waiting_user"
    assert run.projected_waiting_type == "user"
    assert run.projected_waiting_reason == "Confirm the plan"
    assert [event.event_type for event in db.added] == ["waiting_started"]
    assert db.added[0].payload["correlation_id"] == "confirm-plan-1"


@pytest.mark.asyncio
async def test_duplicate_and_stale_trigger_projection_is_a_noop():
    run = _run(
        projected_checkpoint_id="checkpoint-2",
        projected_execution_status="completed",
        lane_held=True,
    )
    checkpoint_1 = _snapshot(
        run,
        checkpoint_id="checkpoint-1",
        version=1,
        status="running",
        created_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    checkpoint_2 = _snapshot(
        run,
        checkpoint_id="checkpoint-2",
        version=2,
        status="completed",
        created_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        parent_checkpoint_id="checkpoint-1",
    )
    db = _FakeSession(_Result(scalar=run))

    result = await RuntimeProjector(_SnapshotSource([checkpoint_2, checkpoint_1])).project_run(
        db,
        tenant_id=run.tenant_id,
        run_id=run.id,
        trigger=RuntimeProjectionTrigger(
            tenant_id=run.tenant_id,
            run_id=run.id,
            checkpoint_id="checkpoint-1",
            checkpoint_version=1,
        ),
    )

    assert result.applied_checkpoint_ids == ()
    assert result.stale_trigger is True
    assert result.authoritative_status == "completed"
    assert run.projected_checkpoint_id == "checkpoint-2"
    assert run.lane_held is True
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_stale_checkpoint_history_cannot_regress_the_watermark():
    run = _run(
        projected_checkpoint_id="checkpoint-2",
        projected_execution_status="completed",
    )
    stale = _snapshot(
        run,
        checkpoint_id="checkpoint-1",
        version=1,
        status="running",
        created_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    db = _FakeSession(_Result(scalar=run))

    with pytest.raises(ProjectionHistoryGapError):
        await RuntimeProjector(_SnapshotSource([stale])).project_run(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    assert run.projected_checkpoint_id == "checkpoint-2"
    assert run.projected_execution_status == "completed"
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_started_at",
    [None, datetime(2026, 7, 13, 8, 30, tzinfo=UTC)],
)
async def test_latest_rebuild_repairs_projection_without_fabricating_events(
    existing_started_at,
):
    run = _run(
        projected_checkpoint_id="pruned-checkpoint",
        projected_execution_status="running",
        lane_held=True,
    )
    run.projected_started_at = existing_started_at
    latest = _snapshot(
        run,
        checkpoint_id="checkpoint-5",
        version=5,
        status="failed",
        created_at=datetime(2026, 7, 13, 11, 0, tzinfo=UTC),
        lifecycle={"error": {"code": "tool_failed", "message": "Tool failed"}},
    )
    db = _FakeSession(_Result(scalar=run))

    result = await RuntimeProjector(_SnapshotSource([latest])).project_run(
        db,
        tenant_id=run.tenant_id,
        run_id=run.id,
        rebuild_latest_on_history_gap=True,
    )

    assert result.history_gap is True
    assert result.applied_checkpoint_ids == ("checkpoint-5",)
    assert result.added_event_types == ()
    assert run.projected_execution_status == "failed"
    assert run.projected_error_code == "tool_failed"
    assert run.projected_last_error == "Tool failed"
    assert run.projected_checkpoint_id == "checkpoint-5"
    assert run.projected_started_at == existing_started_at
    assert run.lane_held is True
    assert db.added == []
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_full_history_rebuild_uses_the_first_started_checkpoint():
    run = _run()
    created_at = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    started_at = datetime(2026, 7, 13, 8, 1, tzinfo=UTC)
    completed_at = datetime(2026, 7, 13, 8, 2, tzinfo=UTC)
    source = _SnapshotSource(
        [
            _snapshot(
                run,
                checkpoint_id="checkpoint-2",
                version=2,
                status="completed",
                created_at=completed_at,
                parent_checkpoint_id="checkpoint-1",
            ),
            _snapshot(
                run,
                checkpoint_id="checkpoint-1",
                version=1,
                status="running",
                created_at=started_at,
                parent_checkpoint_id="checkpoint-0",
            ),
            _snapshot(
                run,
                checkpoint_id="checkpoint-0",
                version=0,
                status="created",
                created_at=created_at,
            ),
        ]
    )
    db = _FakeSession(_Result(scalar=run), _Result(rows=[]))

    await RuntimeProjector(source).project_run(
        db,
        tenant_id=run.tenant_id,
        run_id=run.id,
    )

    assert run.projected_started_at == started_at


@pytest.mark.asyncio
async def test_framework_initial_checkpoint_is_ignored_before_product_projection():
    run = _run()
    running = _snapshot(
        run,
        checkpoint_id="checkpoint-0",
        version=0,
        status="running",
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
        parent_checkpoint_id="framework-initial",
    )
    source = _SnapshotSource([running, _framework_initial_snapshot(run)])
    db = _FakeSession(_Result(scalar=run), _Result(rows=[]))

    result = await RuntimeProjector(source).project_run(
        db,
        tenant_id=run.tenant_id,
        run_id=run.id,
    )

    assert result.applied_checkpoint_ids == ("checkpoint-0",)
    assert result.authoritative_status == "running"
    assert run.projected_checkpoint_id == "checkpoint-0"
    assert all(
        event.source_checkpoint_id == "checkpoint-0" for event in db.added
    )


@pytest.mark.asyncio
async def test_registry_less_non_initial_checkpoint_still_fails_closed():
    run = _run()
    malformed = StateSnapshot(
        values={},
        next=(),
        config={
            "configurable": {
                "thread_id": run.runtime_thread_id,
                "checkpoint_id": "checkpoint-0",
            }
        },
        metadata={"step": 0},
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC).isoformat(),
        parent_config=None,
        tasks=(),
        interrupts=(),
    )
    db = _FakeSession(_Result(scalar=run))

    with pytest.raises(ProjectionCheckpointError, match="registry is required"):
        await RuntimeProjector(_SnapshotSource([malformed])).project_run(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    assert run.projected_checkpoint_id is None
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_cross_tenant_or_missing_run_never_reads_checkpoint_state():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    source = _SnapshotSource([])
    db = _FakeSession(_Result(scalar=None))

    with pytest.raises(ProjectionNotFoundError):
        await RuntimeProjector(source).project_run(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    assert source.configs == []
    assert db.flush_count == 0
    sql = _sql(db.statements[0])
    assert f"agent_runs.tenant_id = '{tenant_id}'" in sql
    assert f"agent_runs.id = '{run_id}'" in sql


@pytest.mark.asyncio
async def test_checkpoint_registry_scope_mismatch_fails_closed():
    run = _run()
    mismatched = _snapshot(
        run,
        checkpoint_id="checkpoint-1",
        version=1,
        status="running",
        created_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
        registry_tenant_id=uuid.uuid4(),
    )
    db = _FakeSession(_Result(scalar=run))

    with pytest.raises(ProjectionCheckpointError, match="registry"):
        await RuntimeProjector(_SnapshotSource([mismatched])).project_run(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    assert run.projected_checkpoint_id is None
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_non_monotonic_checkpoint_versions_fail_closed():
    run = _run(projected_checkpoint_id="checkpoint-1")
    checkpoint_1 = _snapshot(
        run,
        checkpoint_id="checkpoint-1",
        version=2,
        status="running",
        created_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    checkpoint_2 = _snapshot(
        run,
        checkpoint_id="checkpoint-2",
        version=1,
        status="completed",
        created_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        parent_checkpoint_id="checkpoint-1",
    )
    db = _FakeSession(_Result(scalar=run))

    with pytest.raises(ProjectionCheckpointError, match="versions"):
        await RuntimeProjector(_SnapshotSource([checkpoint_2, checkpoint_1])).project_run(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    assert run.projected_checkpoint_id == "checkpoint-1"
    assert db.flush_count == 0
