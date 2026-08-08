"""Regression locks for the checkpoint/Command truth boundary."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.checkpointer import (
    runtime_command_config,
    runtime_thread_config,
)
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandWorker,
    classify_checkpoint,
)
from app.services.agent_runtime.node_executor import (
    CancelSignal,
    RuntimeInvocationCancelled,
)
from app.services.agent_runtime.state import RunInputSnapshots, RunRegistrySnapshot


@pytest.fixture(autouse=True)
def _stub_business_attempt_boundary():
    with patch.object(
        RuntimeCommandWorker,
        "_begin_attempt",
        new_callable=AsyncMock,
    ):
        yield


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, run: AgentRun) -> None:
        self.run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.run)


class _SessionFactory:
    def __init__(self, run: AgentRun) -> None:
        self.run = run

    def __call__(self) -> _Session:
        return _Session(self.run)


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement, _parameters=None) -> _ScalarResult:
        if "pg_try_advisory_lock" in str(statement):
            return _ScalarResult(True)
        if "pg_advisory_unlock" in str(statement):
            return _ScalarResult(True)
        raise AssertionError(str(statement))


class _Engine:
    def connect(self) -> _Connection:
        return _Connection()


class _Reader:
    def __init__(
        self,
        command_observations: list[CheckpointObservation | None],
        latest_observations: list[CheckpointObservation | None] | None = None,
    ) -> None:
        self.command_observations = deque(command_observations)
        self.latest_observations = deque(latest_observations or [])

    async def read_for_command(self, *, connection, run, command):
        del connection, run, command
        return self.command_observations.popleft()

    async def read_latest(self, *, connection, run):
        del connection, run
        return self.latest_observations.popleft()


class _Executor:
    def __init__(self) -> None:
        self.checkpoints: list[CheckpointObservation | None] = []

    async def execute(self, *, connection, run, command, checkpoint) -> None:
        del connection, run, command
        self.checkpoints.append(checkpoint)


class _InterruptedExecutor:
    async def execute(self, *, connection, run, command, checkpoint) -> None:
        del connection, run, command, checkpoint
        raise RuntimeInvocationCancelled(
            CancelSignal(command_id="cancel-pending", reason="user_abort")
        )


class _ProductSync:
    def __init__(self, timeline: list[str], *, error: Exception | None = None) -> None:
        self.timeline = timeline
        self.error = error

    async def handle(self, *, run, command, checkpoint) -> None:
        del run, command, checkpoint
        self.timeline.append("product_sync")
        if self.error is not None:
            raise self.error


def _run(*, thread_id: str = "shared-thread") -> AgentRun:
    run_id = uuid.uuid4()
    return AgentRun(
        id=run_id,
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=thread_id,
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=True,
        delivery_status="pending",
    )


def _command(run: AgentRun, command_type: str = "start") -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type=command_type,
        payload={},
        idempotency_key=f"{command_type}:1",
        status="claimed",
        claimed_by="worker-1",
        claim_expires_at=datetime(2026, 7, 16, 12, 1, tzinfo=UTC),
        attempt_count=1,
        created_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
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
    command: AgentRunCommand,
    *,
    status: str,
    checkpoint_id: str,
) -> CheckpointObservation:
    waiting = status.startswith("waiting_")
    terminal = status in {"completed", "failed", "cancelled"}
    return CheckpointObservation(
        checkpoint_id=checkpoint_id,
        state={
            "registry": _registry(run),
            "snapshots": RunInputSnapshots(
                session_context={},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": status,  # type: ignore[typeddict-item]
                "next_route": "wait" if waiting else ("terminal" if terminal else "model"),
            },
        },
        next_nodes=("wait",) if waiting else (() if terminal else ("model",)),
        tasks=(object(),) if not terminal else (),
        interrupts=(object(),) if waiting else (),
        metadata={
            "clawith_run_id": str(run.id),
            "clawith_command_id": str(command.id),
        },
    )


def test_checkpoint_configs_use_real_thread_and_namespaced_command_metadata() -> None:
    run_id = uuid.uuid4()
    command_id = uuid.uuid4()

    assert runtime_thread_config("session-thread", checkpoint_id="checkpoint-7") == {
        "configurable": {
            "thread_id": "session-thread",
            "checkpoint_id": "checkpoint-7",
        }
    }
    assert runtime_command_config(
        "session-thread",
        run_id=run_id,
        command_id=command_id,
    ) == {
        "configurable": {"thread_id": "session-thread"},
        "metadata": {
            "clawith_run_id": str(run_id),
            "clawith_command_id": str(command_id),
        },
    }


def test_checkpoint_classifier_names_the_not_started_boundary() -> None:
    assert classify_checkpoint(None) == "not_started"


@pytest.mark.asyncio
async def test_runnable_checkpoint_for_same_command_continues_without_resubmitting_input() -> None:
    run = _run()
    command = _command(run)
    runnable = _checkpoint(run, command, status="running", checkpoint_id="checkpoint-accepted")
    stable = _checkpoint(run, command, status="completed", checkpoint_id="checkpoint-stable")
    reader = _Reader([runnable, stable])
    executor = _Executor()
    timeline: list[str] = []

    async def mark_applied(*_args, **kwargs):
        timeline.append(f"applied:{kwargs['applied_checkpoint_id']}")

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(side_effect=mark_applied),
        ),
    ):
        result = await RuntimeCommandWorker(
            session_factory=_SessionFactory(run),  # type: ignore[arg-type]
            lock_engine=_Engine(),  # type: ignore[arg-type]
            checkpoint_reader=reader,
            command_executor=executor,
            post_checkpoint_handler=_ProductSync(timeline),
            claimant="worker-1",
            claim_ttl_seconds=60,
            claim_renew_seconds=10,
            max_attempts=5,
        ).run_once()

    assert result.status == "applied"
    assert executor.checkpoints == [runnable]
    assert timeline == ["applied:checkpoint-stable", "product_sync"]


@pytest.mark.asyncio
async def test_product_sync_failure_never_requeues_or_reexecutes_a_stable_command() -> None:
    run = _run()
    command = _command(run)
    stable = _checkpoint(run, command, status="completed", checkpoint_id="checkpoint-stable")
    reader = _Reader([stable])
    executor = _Executor()
    timeline: list[str] = []

    async def mark_applied(*_args, **kwargs):
        timeline.append(f"applied:{kwargs['applied_checkpoint_id']}")

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(side_effect=mark_applied),
        ),
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as release,
    ):
        result = await RuntimeCommandWorker(
            session_factory=_SessionFactory(run),  # type: ignore[arg-type]
            lock_engine=_Engine(),  # type: ignore[arg-type]
            checkpoint_reader=reader,
            command_executor=executor,
            post_checkpoint_handler=_ProductSync(
                timeline,
                error=RuntimeError("delivery unavailable"),
            ),
            claimant="worker-1",
            claim_ttl_seconds=60,
            claim_renew_seconds=10,
            max_attempts=5,
        ).run_once()

    assert result.status == "reconciled"
    assert executor.checkpoints == []
    assert timeline == ["applied:checkpoint-stable", "product_sync"]
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_preserves_the_last_checkpoint_and_locks_the_real_thread_id() -> None:
    run = _run(thread_id="group-run-thread")
    command = _command(run, "cancel")
    preserved = _checkpoint(run, _command(run), status="waiting_user", checkpoint_id="checkpoint-wait")
    reader = _Reader([None], [preserved])
    executor = _Executor()
    lock_ids: list[str] = []

    async def locked(_engine, thread_id, callback):
        lock_ids.append(thread_id)
        return await callback(object())

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_applied",
            new=AsyncMock(),
        ) as mark_applied,
        patch(
            "app.services.agent_runtime.command_worker.run_with_thread_lock",
            new=locked,
        ),
    ):
        result = await RuntimeCommandWorker(
            session_factory=_SessionFactory(run),  # type: ignore[arg-type]
            lock_engine=_Engine(),  # type: ignore[arg-type]
            checkpoint_reader=reader,
            command_executor=executor,
            post_checkpoint_handler=_ProductSync([]),
            claimant="worker-1",
            claim_ttl_seconds=60,
            claim_renew_seconds=10,
            max_attempts=5,
        ).run_once()

    assert result.status == "applied"
    assert lock_ids == ["group-run-thread"]
    assert executor.checkpoints == []
    assert mark_applied.await_args.kwargs["applied_checkpoint_id"] == "checkpoint-wait"


@pytest.mark.asyncio
async def test_active_invocation_is_rejected_before_cancel_applies_without_graph_retry() -> None:
    run = _run(thread_id="direct-session-thread")
    command = _command(run, "start")
    reader = _Reader([None], [None])

    with (
        patch(
            "app.services.agent_runtime.command_worker.claim_next_command",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "app.services.agent_runtime.command_worker.mark_command_rejected",
            new=AsyncMock(),
        ) as rejected,
        patch(
            "app.services.agent_runtime.command_worker.release_command_claim",
            new=AsyncMock(),
        ) as released,
    ):
        result = await RuntimeCommandWorker(
            session_factory=_SessionFactory(run),  # type: ignore[arg-type]
            lock_engine=_Engine(),  # type: ignore[arg-type]
            checkpoint_reader=reader,
            command_executor=_InterruptedExecutor(),
            post_checkpoint_handler=_ProductSync([]),
            claimant="worker-1",
            claim_ttl_seconds=60,
            claim_renew_seconds=10,
            max_attempts=5,
        ).run_once()

    assert result.status == "rejected"
    assert result.error_code == "cancelled_before_apply"
    rejected.assert_awaited_once()
    released.assert_not_awaited()
