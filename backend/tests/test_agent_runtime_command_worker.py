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
    def __init__(self, *observations: CheckpointObservation | None) -> None:
        self.observations = deque(observations)
        self.calls: list[tuple[object, RuntimeRunRecord]] = []

    async def read_latest(self, *, connection, run):
        self.calls.append((connection, run))
        if not self.observations:
            raise AssertionError("unexpected checkpoint read")
        return self.observations.popleft()


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
    command_ids: list[str] | None = None,
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
            "next_route": "terminal" if status in {"completed", "failed", "cancelled"} else "model",
            "last_applied_command_ids": command_ids or [],
        },
    }
    return CheckpointObservation(checkpoint_id=checkpoint_id, state=state)


def _worker(
    *,
    timeline: list[str],
    run: AgentRun,
    reader: _Reader,
    executor: _Executor,
    post_checkpoint_handler: _PostCheckpointHandler | None = None,
    acquired: bool = True,
    claim_renew_seconds: float = 10,
) -> RuntimeCommandWorker:
    return RuntimeCommandWorker(
        session_factory=_SessionFactory(timeline, run),  # type: ignore[arg-type]
        lock_engine=_Engine(_Connection(timeline, acquired=acquired)),  # type: ignore[arg-type]
        checkpoint_reader=reader,
        command_executor=executor,
        post_checkpoint_handler=post_checkpoint_handler or _PostCheckpointHandler(timeline),
        claimant="worker-1",
        claim_ttl_seconds=60,
        claim_renew_seconds=claim_renew_seconds,
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_claim_commits_before_lock_and_heartbeat_runs_during_execution() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "start")
    renewal_seen = asyncio.Event()
    reader = _Reader(
        None,
        _checkpoint(run, status="running", command_ids=[str(command.id)]),
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
    assert timeline.index("post_checkpoint:checkpoint-1") < timeline.index("applied:checkpoint-1")
    assert timeline.index("applied:checkpoint-1") < timeline.index("lock_release")
    renew_claim.assert_awaited()
    mark_applied.assert_awaited_once()
    _, run_record, command_record, initial_checkpoint = executor.calls[0]
    assert isinstance(run_record, RuntimeRunRecord)
    assert not isinstance(run_record, AgentRun)
    assert isinstance(command_record, RuntimeCommandRecord)
    assert initial_checkpoint is None


@pytest.mark.asyncio
async def test_checkpoint_reconciliation_marks_applied_without_invoking_graph() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        _checkpoint(
            run,
            status="waiting_user",
            command_ids=[str(command.id)],
            checkpoint_id="checkpoint-reconciled",
        )
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
async def test_post_checkpoint_failure_releases_command_before_marking_applied() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    reader = _Reader(
        _checkpoint(
            run,
            status="waiting_user",
            command_ids=[str(command.id)],
            checkpoint_id="checkpoint-side-effects",
        )
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

    assert result.status == "retry"
    assert result.error_code == "post_checkpoint_handler_failed"
    assert executor.calls == []
    assert release.await_args.kwargs["error_code"] == "post_checkpoint_handler_failed"
    mark_applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_cancel_is_rejected_from_checkpoint_not_projection() -> None:
    timeline: list[str] = []
    run = _run()
    run.projected_execution_status = "running"
    command = _command(run, "cancel")
    reader = _Reader(_checkpoint(run, status="completed"))
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
    assert result.error_code == "terminal_cancel"
    assert reject.await_args.kwargs["error_code"] == "terminal_cancel"
    assert executor.calls == []
    assert "projected_" not in inspect.getsource(RuntimeCommandWorker)


@pytest.mark.asyncio
async def test_missing_command_id_after_invoke_returns_command_to_pending() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    active = _checkpoint(run, status="waiting_user")
    reader = _Reader(active, _checkpoint(run, status="running", checkpoint_id="checkpoint-2"))
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
async def test_cancel_requires_cancelled_checkpoint_even_when_command_id_is_present() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run, "cancel")
    reader = _Reader(
        _checkpoint(run, status="running"),
        _checkpoint(
            run,
            status="running",
            command_ids=[str(command.id)],
            checkpoint_id="checkpoint-after-cancel",
        ),
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
    assert result.error_code == "cancel_not_observed"
    assert release.await_args.kwargs["error_code"] == "cancel_not_observed"
    mark_applied.assert_not_awaited()
    assert len(executor.calls) == 1


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
async def test_checkpoint_identity_mismatch_is_reclaimable_and_not_executed() -> None:
    timeline: list[str] = []
    run = _run()
    command = _command(run)
    wrong_registry = replace(_registry(run), tenant_id=str(uuid.uuid4()))
    reader = _Reader(_checkpoint(run, status="waiting_user", registry=wrong_registry))
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
    reader = _Reader(None)
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
    reader = _Reader(_checkpoint(run, status="waiting_user"))
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
    reader = _Reader(_checkpoint(run, status="waiting_user"))
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
