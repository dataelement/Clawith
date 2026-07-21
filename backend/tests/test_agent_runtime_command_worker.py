"""Command Worker orchestration tests without a database or Graph driver."""

from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
import asyncio
import inspect
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    CommandExecutionRejected,
    RuntimeCommandRecord,
    RuntimeCommandWorker,
    RuntimeRunRecord,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)
from app.services.agent_runtime.tool_execution import (
    ToolExecutionReconciliationPending,
)


@pytest.fixture(autouse=True)
def _stub_business_attempt_boundary():
    with patch.object(
        RuntimeCommandWorker,
        "_begin_attempt",
        new_callable=AsyncMock,
    ) as begin_attempt:
        yield begin_attempt


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def __aenter__(self):
        self.timeline.append("transaction_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.timeline.append("transaction_exit")
        return False


class _Session:
    def __init__(self, timeline: list[str], run: AgentRun | None) -> None:
        self.timeline = timeline
        self.run = run

    async def __aenter__(self):
        self.timeline.append("session_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.timeline.append("session_exit")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self.timeline)

    async def execute(self, _statement) -> _ScalarResult:
        self.timeline.append("load_run")
        return _ScalarResult(self.run)

    async def flush(self) -> None:
        self.timeline.append("flush")


class _SessionFactory:
    def __init__(self, timeline: list[str], run: AgentRun | None) -> None:
        self.timeline = timeline
        self.run = run
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return _Session(self.timeline, self.run)


class _Connection:
    def __init__(self, timeline: list[str], *, acquired: bool = True) -> None:
        self.timeline = timeline
        self.acquired = acquired

    async def __aenter__(self):
        self.timeline.append("lock_connection_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.timeline.append("lock_connection_exit")
        return False

    async def execute(self, statement, _parameters=None) -> _ScalarResult:
        sql = str(statement)
        if "pg_try_advisory_lock" in sql:
            self.timeline.append("lock_acquire")
            return _ScalarResult(self.acquired)
        if "pg_advisory_unlock" in sql:
            self.timeline.append("lock_release")
            return _ScalarResult(True)
        raise AssertionError(f"unexpected lock SQL: {sql}")


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


class _Reader:
    def __init__(
        self,
        *,
        command: tuple[CheckpointObservation | None, ...] = (),
        latest: tuple[CheckpointObservation | None, ...] = (),
    ) -> None:
        self.command_observations = deque(command)
        self.latest_observations = deque(latest)
        self.calls: list[tuple[object, RuntimeRunRecord]] = []

    async def read_for_command(self, *, connection, run, command):
        del command
        self.calls.append((connection, run))
        if not self.command_observations:
            raise AssertionError("unexpected command checkpoint read")
        return self.command_observations.popleft()

    async def read_latest(self, *, connection, run):
        self.calls.append((connection, run))
        if not self.latest_observations:
            raise AssertionError("unexpected latest checkpoint read")
        return self.latest_observations.popleft()


class _Executor:
    def __init__(
        self,
        timeline: list[str],
        *,
        wait_for: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.timeline = timeline
        self.wait_for = wait_for
        self.error = error
        self.calls: list[tuple[object, RuntimeRunRecord, RuntimeCommandRecord, CheckpointObservation | None]] = []

    async def execute(self, *, connection, run, command, checkpoint) -> None:
        self.timeline.append("executor_start")
        self.calls.append((connection, run, command, checkpoint))
        if self.wait_for is not None:
            await asyncio.wait_for(self.wait_for.wait(), timeout=1)
        if self.error is not None:
            raise self.error
        self.timeline.append("executor_end")


class _PostCheckpointHandler:
    def __init__(self, timeline: list[str], *, error: Exception | None = None) -> None:
        self.timeline = timeline
        self.error = error
        self.calls: list[tuple[RuntimeRunRecord, RuntimeCommandRecord, CheckpointObservation]] = []

    async def handle(self, *, run, command, checkpoint) -> None:
        self.timeline.append(f"post_checkpoint:{checkpoint.checkpoint_id}")
        self.calls.append((run, command, checkpoint))
        if self.error is not None:
            raise self.error


class _PreCommandHandler:
    def __init__(self, timeline: list[str], *, error: Exception | None = None) -> None:
        self.timeline = timeline
        self.error = error
        self.calls = []

    async def handle(self, *, run, command, checkpoint) -> None:
        self.timeline.append("pre_command")
        self.calls.append((run, command, checkpoint))
        if self.error is not None:
            raise self.error


def _run(*, tenant_id: uuid.UUID | None = None) -> AgentRun:
    run_id = uuid.uuid4()
    return AgentRun(
        id=run_id,
        tenant_id=tenant_id or uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        source_type="chat",
        goal="Answer the user",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )


def _command(run: AgentRun, command_type: str = "resume") -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type=command_type,
        payload={"value": "continue"},
        actor_user_id=uuid.uuid4(),
        idempotency_key=f"{command_type}:1",
        status="claimed",
        claimed_by="worker-1",
        claim_expires_at=datetime(2026, 7, 13, 12, 1, tzinfo=UTC),
        attempt_count=1,
        created_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )


def _registry(run: AgentRun) -> RunRegistrySnapshot:
    return RunRegistrySnapshot(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        goal=run.goal,
        run_kind=run.run_kind,
        source_type=run.source_type,
        model_id=str(run.model_id),
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        agent_id=str(run.agent_id),
        session_id=str(run.session_id),
    )


def _checkpoint(
    run: AgentRun,
    *,
    status: str,
    command: AgentRunCommand | None = None,
    checkpoint_id: str = "checkpoint-1",
    registry: RunRegistrySnapshot | None = None,
) -> CheckpointObservation:
    state: RuntimeGraphState = {
        "registry": registry or _registry(run),
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": status,  # type: ignore[typeddict-item]
            "next_route": (
                "terminal"
                if status in {"completed", "failed", "cancelled"}
                else ("wait" if status.startswith("waiting_") else "model")
            ),
        },
    }
    terminal = status in {"completed", "failed", "cancelled"}
    waiting = status.startswith("waiting_")
    metadata = {"clawith_run_id": str(run.id)}
    if command is not None:
        metadata["clawith_command_id"] = str(command.id)
    return CheckpointObservation(
        checkpoint_id=checkpoint_id,
        state=state,
        next_nodes=() if terminal else (("wait",) if waiting else ("model",)),
        tasks=() if terminal else (object(),),
        interrupts=(object(),) if waiting else (),
        metadata=metadata,
    )


def _worker(
    *,
    timeline: list[str],
    run: AgentRun,
    reader: _Reader,
    executor: _Executor,
    post_checkpoint_handler: _PostCheckpointHandler | None = None,
    pre_command_handler: _PreCommandHandler | None = None,
    acquired: bool = True,
    claim_renew_seconds: float = 10,
) -> RuntimeCommandWorker:
    return RuntimeCommandWorker(
        session_factory=_SessionFactory(timeline, run),  # type: ignore[arg-type]
        lock_engine=_Engine(_Connection(timeline, acquired=acquired)),  # type: ignore[arg-type]
        checkpoint_reader=reader,
        command_executor=executor,
        pre_command_handler=pre_command_handler,
        post_checkpoint_handler=post_checkpoint_handler or _PostCheckpointHandler(timeline),
        claimant="worker-1",
        claim_ttl_seconds=60,
        claim_renew_seconds=claim_renew_seconds,
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_pre_command_side_effect_runs_after_claim_commit_and_before_graph() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    observed = _checkpoint(run, status="completed", command=command)
    reader = _Reader(command=(None, observed), latest=(None,))
    executor = _Executor(timeline)
    pre_handler = _PreCommandHandler(timeline)
    worker = _worker(
        timeline=timeline,
        run=run,
        reader=reader,
        executor=executor,
        pre_command_handler=pre_handler,
    )

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ),
    ):
        result = await worker.run_once()

    assert result.status == "applied"
    assert pre_handler.calls == [(pre_handler.calls[0][0], pre_handler.calls[0][1], None)]
    assert pre_handler.calls[0][0].run_id == run.id
    assert pre_handler.calls[0][1].id == command.id
    assert timeline.index("transaction_exit") < timeline.index("pre_command")
    assert timeline.index("pre_command") < timeline.index("executor_start")


@pytest.mark.asyncio
async def test_claim_commits_before_lock_and_heartbeat_runs_during_execution() -> None:
    timeline: list[str] = []
    run = _run()
    trigger_message_id = uuid.uuid4()
    trigger_created_at = datetime(2026, 7, 13, 11, 59, tzinfo=UTC)
    run.source_id = str(trigger_message_id)
    run.scheduling_position_created_at = trigger_created_at
    run.scheduling_position_id = trigger_message_id
    command = _command(run, "start")
    renewal_seen = asyncio.Event()
    reader = _Reader(
        command=(
            None,
            _checkpoint(run, status="completed", command=command),
        ),
        latest=(None,),
    )
    executor = _Executor(timeline, wait_for=renewal_seen)

    async def renew(*_args, **_kwargs):
        timeline.append("claim_renewed")
        renewal_seen.set()

    async def applied(*_args, **kwargs):
        timeline.append(f"applied:{kwargs['applied_checkpoint_id']}")

    post_checkpoint_handler = _PostCheckpointHandler(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.renew_command_claim",
            new=AsyncMock(side_effect=renew),
        ) as renew_claim,
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(side_effect=applied),
        ) as mark_applied,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
            post_checkpoint_handler=post_checkpoint_handler,
            claim_renew_seconds=0.01,
        ).run_once()

    assert result.status == "applied"
    assert result.checkpoint_id == "checkpoint-1"
    assert timeline.index("transaction_exit") < timeline.index("lock_acquire")
    assert timeline.index("claim_renewed") < timeline.index("executor_end")
    assert timeline.index("applied:checkpoint-1") < timeline.index("post_checkpoint:checkpoint-1")
    assert timeline.index("post_checkpoint:checkpoint-1") < timeline.index("lock_release")
    renew_claim.assert_awaited()
    mark_applied.assert_awaited_once()
    _, run_record, command_record, initial_checkpoint = executor.calls[0]
    assert isinstance(run_record, RuntimeRunRecord)
    assert not isinstance(run_record, AgentRun)
    assert run_record.source_id == str(trigger_message_id)
    assert run_record.scheduling_position_created_at == trigger_created_at
    assert run_record.scheduling_position_id == trigger_message_id
    assert isinstance(command_record, RuntimeCommandRecord)
    assert initial_checkpoint is None


@pytest.mark.asyncio
async def test_checkpoint_reconciliation_marks_applied_without_invoking_graph() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        command=(_checkpoint(
            run,
            status="waiting_user",
            command=command,
            checkpoint_id="checkpoint-reconciled",
        ),)
    )
    executor = _Executor(timeline)
    post_checkpoint_handler = _PostCheckpointHandler(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ) as mark_applied,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
            post_checkpoint_handler=post_checkpoint_handler,
        ).run_once()

    assert result.status == "reconciled"
    assert result.checkpoint_id == "checkpoint-reconciled"
    assert executor.calls == []
    assert post_checkpoint_handler.calls[0][2].checkpoint_id == "checkpoint-reconciled"
    assert mark_applied.await_args.kwargs["applied_checkpoint_id"] == "checkpoint-reconciled"


@pytest.mark.asyncio
async def test_post_checkpoint_failure_does_not_requeue_an_applied_command() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        command=(_checkpoint(
            run,
            status="waiting_user",
            command=command,
            checkpoint_id="checkpoint-side-effects",
        ),)
    )
    executor = _Executor(timeline)
    post_checkpoint_handler = _PostCheckpointHandler(
        timeline,
        error=RuntimeError("delivery unavailable"),
    )

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ) as mark_applied,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
            post_checkpoint_handler=post_checkpoint_handler,
        ).run_once()

    assert result.status == "reconciled"
    assert executor.calls == []
    release.assert_not_awaited()
    mark_applied.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_cancel_is_rejected_from_checkpoint_not_projection() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "cancel")
    reader = _Reader(
        command=(None,),
        latest=(_checkpoint(run, status="completed"),),
    )
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_rejected",
            new=AsyncMock(),
        ) as reject,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "rejected"
    assert result.error_code == "already_terminal"
    assert reject.await_args.kwargs["error_code"] == "already_terminal"
    assert executor.calls == []
    assert "projected_" not in inspect.getsource(RuntimeCommandWorker)


@pytest.mark.asyncio
async def test_missing_command_id_after_invoke_returns_command_to_pending() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    active = _checkpoint(run, status="waiting_user")
    reader = _Reader(
        command=(None, None),
        latest=(active,),
    )
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ) as mark_applied,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "retry"
    assert result.error_code == "checkpoint_not_observed"
    assert release.await_args.kwargs["error_code"] == "checkpoint_not_observed"
    mark_applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_preserves_latest_checkpoint_without_invoking_graph() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "cancel")
    preserved = _checkpoint(run, status="waiting_user", checkpoint_id="checkpoint-before-cancel")
    reader = _Reader(
        command=(None,),
        latest=(preserved,),
    )
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ) as mark_applied,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "applied"
    assert result.checkpoint_id == "checkpoint-before-cancel"
    release.assert_not_awaited()
    assert mark_applied.await_args.kwargs["applied_checkpoint_id"] == "checkpoint-before-cancel"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_lock_contention_never_reads_or_invokes_and_releases_claim() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader()
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
            acquired=False,
        ).run_once()

    assert result.status == "retry"
    assert result.error_code == "thread_lock_busy"
    assert reader.calls == []
    assert executor.calls == []
    assert release.await_args.kwargs["error_code"] == "thread_lock_busy"


@pytest.mark.asyncio
async def test_active_tool_fence_defer_refunds_business_attempt_and_releases_claim(
    _stub_business_attempt_boundary,
) -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    worker = _worker(
        timeline=timeline,
        run=run,
        reader=_Reader(command=(None,), latest=(None,)),
        executor=_Executor(
            timeline,
            error=ToolExecutionReconciliationPending(
                "group_workspace_active_lease",
                "another invocation still owns the Group workspace operation",
                defer_without_attempt=True,
            ),
        ),
    )
    worker._defer_without_attempt = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "app.services.agent_runtime.command_worker.claim_next_command",
        new=AsyncMock(return_value=command),
    ):
        result = await worker.run_once()

    assert result.status == "retry"
    assert result.error_code == "group_workspace_active_lease"
    _stub_business_attempt_boundary.assert_awaited_once()
    worker._defer_without_attempt.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_defer_release_atomically_refunds_the_consumed_attempt() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    command.attempt_count = 3
    worker = _worker(
        timeline=timeline,
        run=run,
        reader=_Reader(),
        executor=_Executor(timeline),
    )

    with patch(
        "app.services.agent_runtime.command_worker.release_command_claim",
        new=AsyncMock(return_value=command),
    ) as release:
        await worker._defer_without_attempt(
            RuntimeCommandRecord(
                id=command.id,
                tenant_id=command.tenant_id,
                run_id=command.run_id,
                command_type="start",
                payload=dict(command.payload),
                actor_user_id=command.actor_user_id,
                actor_agent_id=command.actor_agent_id,
                attempt_count=command.attempt_count,
            ),
            "group_workspace_active_lease",
        )

    assert command.attempt_count == 2
    release.assert_awaited_once()
    assert "flush" in timeline


@pytest.mark.asyncio
async def test_exhausted_command_is_explicitly_quarantined_before_lock_or_graph(
    _stub_business_attempt_boundary,
) -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    command.attempt_count = 5

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_rejected",
            new=AsyncMock(),
        ) as rejected,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=_Reader(),
            executor=_Executor(timeline),
        ).run_once()

    assert result.status == "rejected"
    assert result.error_code == "reconciliation_required"
    rejected.assert_awaited_once()
    _stub_business_attempt_boundary.assert_not_awaited()
    assert "lock_acquire" not in timeline


@pytest.mark.asyncio
async def test_business_attempt_starts_only_after_real_thread_lock(
    _stub_business_attempt_boundary,
) -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    observed = _checkpoint(run, status="completed", command=command)
    _stub_business_attempt_boundary.side_effect = lambda _command: timeline.append(
        "business_attempt"
    )

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ),
    ):
        await _worker(
            timeline=timeline,
            run=run,
            reader=_Reader(command=(None, observed), latest=(None,)),
            executor=_Executor(timeline),
        ).run_once()

    assert timeline.index("lock_acquire") < timeline.index("business_attempt")


@pytest.mark.asyncio
async def test_checkpoint_metadata_mismatch_is_reclaimable_and_not_executed() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    checkpoint = _checkpoint(
        run,
        status="waiting_user",
        command=command,
    )
    checkpoint = replace(
        checkpoint,
        metadata={
            **checkpoint.metadata,
            "clawith_run_id": str(uuid.uuid4()),
        },
    )
    reader = _Reader(
        command=(checkpoint,)
    )
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "retry"
    assert result.error_code == "checkpoint_identity_mismatch"
    assert executor.calls == []
    assert release.await_args.kwargs["error_code"] == "checkpoint_identity_mismatch"


@pytest.mark.asyncio
async def test_resume_without_checkpoint_is_rejected_without_execution() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "resume")
    reader = _Reader(command=(None,), latest=(None,))
    executor = _Executor(timeline)

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_rejected",
            new=AsyncMock(),
        ) as reject,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "rejected"
    assert result.error_code == "thread_not_started"
    assert reject.await_args.kwargs["error_code"] == "thread_not_started"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_unexpected_driver_error_releases_claim_then_propagates() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        command=(None,),
        latest=(_checkpoint(run, status="waiting_user"),),
    )
    executor = _Executor(timeline, error=RuntimeError("provider unavailable"))

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
    ):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await _worker(
                timeline=timeline,
                run=run,
                reader=reader,
                executor=executor,
            ).run_once()

    assert release.await_args.kwargs["error_code"] == "command_execution_failed"


@pytest.mark.asyncio
async def test_driver_can_deterministically_reject_invalid_resume() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        command=(None,),
        latest=(_checkpoint(run, status="waiting_user"),),
    )
    executor = _Executor(
        timeline,
        error=CommandExecutionRejected("invalid_resume", "correlation ID does not match"),
    )

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_rejected",
            new=AsyncMock(),
        ) as reject,
    ):
        result = await _worker(
            timeline=timeline,
            run=run,
            reader=reader,
            executor=executor,
        ).run_once()

    assert result.status == "rejected"
    assert result.error_code == "invalid_resume"
    assert reject.await_args.kwargs["error_code"] == "invalid_resume"
