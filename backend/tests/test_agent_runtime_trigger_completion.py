"""Terminal Runtime checkpoint projection into TriggerExecution state."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.trigger_execution import TriggerExecution
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)
from app.services.agent_runtime.trigger_completion import (
    TriggerRuntimeCompletionHandler,
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
    source_type: str = "trigger",
    status: str = "completed",
) -> tuple[
    RuntimeRunRecord,
    CheckpointObservation,
    AgentRun,
    TriggerExecution,
    ChatSession,
]:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    execution = TriggerExecution(
        id=uuid.uuid4(),
        trigger_id=trigger_id,
        agent_id=agent_id,
        source="webhook",
        status="processing",
        idempotency_key="delivery-1",
        payload={},
        payload_text="",
    )
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="trigger",
        agent_id=agent_id,
        user_id=uuid.uuid4(),
        participant_id=uuid.uuid4(),
        title="Reflection",
        source_channel="trigger",
        is_group=False,
        is_primary=False,
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="handle trigger",
        run_kind="background",
        source_type=source_type,
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(agent_id),
        session_id=str(session.id),
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
        "final_answer": "Upstream is ready" if status == "completed" else None,
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
    checkpoint = CheckpointObservation(
        checkpoint_id="checkpoint-terminal",
        state=state,
    )
    stored_run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session.id,
        source_type="trigger",
        source_id=str(trigger_id),
        source_execution_id=str(execution.id),
        goal="handle trigger",
        run_kind="background",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="not_required",
    )
    return run, checkpoint, stored_run, execution, session


@pytest.mark.asyncio
async def test_completed_checkpoint_settles_execution_and_reflection_once() -> None:
    run, checkpoint, stored_run, execution, session = _records()
    db = _Session(stored_run, None, execution, session)
    finished_at = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    handler = TriggerRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        clock=lambda: finished_at,
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert execution.status == "completed"
    assert execution.finished_at == finished_at
    assert execution.last_error is None
    assert session.last_message_at == finished_at
    assert db.flushes == 1
    assert len(db.added) == 1
    message = db.added[0]
    assert isinstance(message, ChatMessage)
    assert message.id == uuid.uuid5(
        run.run_id,
        "trigger-terminal:checkpoint-terminal",
    )
    assert message.content == "Upstream is ready"
    assert message.conversation_id == str(session.id)


@pytest.mark.asyncio
async def test_existing_reflection_receipt_makes_reconciliation_idempotent() -> None:
    run, checkpoint, stored_run, execution, _ = _records()
    receipt_id = uuid.uuid5(run.run_id, "trigger-terminal:checkpoint-terminal")
    db = _Session(stored_run, receipt_id)
    handler = TriggerRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert execution.status == "processing"
    assert db.added == []
    assert db.flushes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_content", "expected_error"),
    [
        ("failed", "❌ 触发器执行失败：model_call_failed", "model_call_failed"),
        ("cancelled", "⏹️ 触发器执行已取消：user_abort", "user_abort"),
    ],
)
async def test_unsuccessful_terminal_checkpoint_marks_execution_failed(
    status: str,
    expected_content: str,
    expected_error: str,
) -> None:
    run, checkpoint, stored_run, execution, session = _records(status=status)
    db = _Session(stored_run, None, execution, session)
    handler = TriggerRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert execution.status == "failed"
    assert execution.last_error == expected_error
    assert isinstance(db.added[0], ChatMessage)
    assert db.added[0].content == expected_content


@pytest.mark.asyncio
async def test_non_trigger_run_is_ignored_without_opening_a_session() -> None:
    run, checkpoint, _, _, _ = _records(source_type="chat")
    factory = _SessionFactory()
    handler = TriggerRuntimeCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert factory.calls == 0
