"""Terminal Runtime checkpoint projection into Task product state."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.models.task import Task, TaskLog
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)
from app.services.agent_runtime.task_completion import (
    TaskRuntimeCompletionHandler,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.results.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return self.sessions.popleft()


def _records(
    *,
    source_type: str = "task",
    status: str = "completed",
) -> tuple[RuntimeRunRecord, CheckpointObservation, AgentRun, Task]:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run_id = uuid.uuid4()
    task = Task(
        id=uuid.uuid4(),
        agent_id=agent_id,
        title="Complete report",
        type="todo",
        status="doing",
        priority="medium",
        created_by=uuid.uuid4(),
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="complete report",
        run_kind="background",
        source_type=source_type,
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(agent_id),
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
    )
    lifecycle = {
        "status": status,
        "next_route": "terminal",
        "final_answer": "Report completed" if status == "completed" else None,
        "reason": "user_abort" if status == "cancelled" else None,
        "error": {"code": "model_call_failed"} if status == "failed" else None,
    }
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": lifecycle,  # type: ignore[typeddict-item]
    }
    checkpoint = CheckpointObservation(checkpoint_id="checkpoint-terminal", state=state)
    stored_run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="task",
        source_id=str(task.id),
        goal="complete report",
        run_kind="background",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="not_required",
    )
    return run, checkpoint, stored_run, task


@pytest.mark.asyncio
async def test_completed_checkpoint_marks_task_done_and_writes_one_receipt_log() -> None:
    run, checkpoint, stored_run, task = _records()
    session = _Session(stored_run, None, task)
    completed_at = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
    handler = TaskRuntimeCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
        clock=lambda: completed_at,
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert task.status == "done"
    assert task.completed_at == completed_at
    assert session.flushes == 1
    assert len(session.added) == 1
    log = session.added[0]
    assert isinstance(log, TaskLog)
    assert log.id == uuid.uuid5(run.run_id, "task-terminal:checkpoint-terminal")
    assert log.content == "✅ 任务完成\n\nReport completed"


@pytest.mark.asyncio
async def test_existing_terminal_log_makes_reconciliation_idempotent() -> None:
    run, checkpoint, stored_run, task = _records()
    receipt_id = uuid.uuid5(run.run_id, "task-terminal:checkpoint-terminal")
    session = _Session(stored_run, receipt_id)
    handler = TaskRuntimeCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert task.status == "doing"
    assert session.added == []
    assert session.flushes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_content"),
    [
        ("failed", "❌ 任务执行失败：model_call_failed"),
        ("cancelled", "⏹️ 任务执行已取消：user_abort"),
    ],
)
async def test_unsuccessful_terminal_checkpoint_returns_task_to_pending(
    status: str,
    expected_content: str,
) -> None:
    run, checkpoint, stored_run, task = _records(status=status)
    session = _Session(stored_run, None, task)
    handler = TaskRuntimeCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert task.status == "pending"
    assert task.completed_at is None
    assert isinstance(session.added[0], TaskLog)
    assert session.added[0].content == expected_content


@pytest.mark.asyncio
async def test_completed_supervision_returns_to_pending_and_logs_result() -> None:
    run, checkpoint, stored_run, task = _records()
    task.type = "supervision"
    session = _Session(stored_run, None, task)
    handler = TaskRuntimeCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert task.status == "pending"
    assert task.completed_at is None
    assert isinstance(session.added[0], TaskLog)
    assert session.added[0].content == "✅ 督办执行完成\n\nReport completed"


@pytest.mark.asyncio
async def test_non_task_run_is_ignored_without_opening_a_session() -> None:
    run, checkpoint, _, _ = _records(source_type="chat")
    factory = _SessionFactory()
    handler = TaskRuntimeCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert factory.calls == 0
