"""A2A terminal target projection and callback tests."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.gateway_message import GatewayMessage
from app.services.agent_runtime.a2a_completion import (
    A2ARuntimeCompletionHandler,
)
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.contracts import ResumeRunCommand, RunHandle
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    def __init__(self, db: "_Session") -> None:
        self.db = db

    async def __aenter__(self):
        self.db.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.db.in_transaction = False
        return False


class _Session:
    def __init__(
        self,
        *results: object,
        records: dict[tuple[type, uuid.UUID], object] | None = None,
    ) -> None:
        self.results = deque(results)
        self.records = records or {}
        self.added: list[object] = []
        self.flushes = 0
        self.in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.results.popleft())

    async def get(self, model, identity):
        return self.records.get((model, identity))

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
    mode: str = "task_delegate",
    status: str = "completed",
) -> tuple[
    RuntimeRunRecord,
    CheckpointObservation,
    AgentRun,
    AgentRun,
    Agent,
    ChatSession,
]:
    tenant_id = uuid.uuid4()
    source_agent_id = uuid.uuid4()
    target_agent_id = uuid.uuid4()
    source_run_id = uuid.uuid4()
    target_run_id = uuid.uuid4()
    session_id = uuid.uuid4()
    correlation_id = f"a2a:{mode}:{uuid.uuid4()}"
    target_agent = Agent(
        id=target_agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Researcher",
        status="idle",
        is_expired=False,
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="a2a",
        agent_id=min((source_agent_id, target_agent_id), key=str),
        peer_agent_id=max((source_agent_id, target_agent_id), key=str),
        user_id=uuid.uuid4(),
        title="Coordinator ↔ Researcher",
        source_channel="agent",
        is_group=False,
        is_primary=False,
    )
    source_run = AgentRun(
        id=source_run_id,
        tenant_id=tenant_id,
        agent_id=source_agent_id,
        source_type="chat",
        source_id=str(uuid.uuid4()),
        goal="Coordinate answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(source_run_id),
        graph_name="runtime",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )
    target_run = AgentRun(
        id=target_run_id,
        tenant_id=tenant_id,
        agent_id=target_agent_id,
        session_id=session_id,
        source_type="a2a",
        source_id=str(session_id),
        source_execution_id=f"a2a:{uuid.uuid4()}",
        correlation_id=correlation_id,
        origin_user_id=session.user_id,
        origin_agent_id=source_agent_id,
        parent_run_id=source_run_id,
        root_run_id=source_run_id,
        goal="Research the facts",
        run_kind="delegated",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(target_run_id),
        graph_name="runtime",
        graph_version="v1",
        lane_held=False,
        delivery_status="not_required",
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(target_run_id),
        goal=target_run.goal,
        run_kind="delegated",
        source_type="a2a",
        model_id=str(target_run.model_id),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(target_agent_id),
        session_id=str(session_id),
        parent_run_id=str(source_run_id),
        root_run_id=str(source_run_id),
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=target_run_id,
        thread_id=str(target_run_id),
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
        "final_answer": "Verified research result" if status == "completed" else None,
        "result_summary": (
            {"summary": "Verified research result", "artifact_refs": ["doc:1"]}
            if status == "completed"
            else None
        ),
        "reason": "target_cancelled" if status == "cancelled" else None,
        "error": {"code": "target_failed"} if status == "failed" else None,
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
    return (
        run,
        CheckpointObservation(checkpoint_id="target-terminal", state=state),
        target_run,
        source_run,
        target_agent,
        session,
    )


@pytest.mark.asyncio
async def test_completed_request_projects_message_and_resumes_source_atomically() -> None:
    run, checkpoint, target_run, source_run, target_agent, session = _records()
    db = _Session(target_run, None, source_run, target_agent, session)
    participant = SimpleNamespace(id=uuid.uuid4())
    handle = RunHandle(
        tenant_id=run.tenant_id,
        run_id=source_run.id,
        thread_id=str(source_run.id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    async def resume_source(command):
        assert db.in_transaction
        return handle

    with (
        patch(
            "app.services.agent_runtime.a2a_completion.get_or_create_agent_participant",
            new=AsyncMock(return_value=participant),
        ),
        patch(
            "app.services.agent_runtime.a2a_completion.RuntimeCommandIntake.resume_run",
            new=AsyncMock(side_effect=resume_source),
        ) as resume_run,
    ):
        await A2ARuntimeCompletionHandler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        ).handle(run=run, checkpoint=checkpoint)

    assert db.flushes == 1
    assert len(db.added) == 1
    message = db.added[0]
    assert isinstance(message, ChatMessage)
    assert message.id == uuid.uuid5(
        run.run_id,
        "a2a-terminal:target-terminal",
    )
    assert message.content == "Verified research result"
    assert message.conversation_id == str(session.id)
    assert message.participant_id == participant.id
    command = resume_run.await_args.args[0]
    assert isinstance(command, ResumeRunCommand)
    assert command.run_id == source_run.id
    assert command.actor_agent_id == target_agent.id
    assert command.payload == {
        "resume_type": "agent_result",
        "correlation_id": target_run.correlation_id,
        "payload": {
            "target_run_id": str(target_run.id),
            "target_agent_id": str(target_agent.id),
            "status": "completed",
            "result_summary": "Verified research result",
            "artifact_refs": ["doc:1"],
            "error": None,
        },
    }


@pytest.mark.asyncio
async def test_gateway_target_completion_queues_reply_without_a_source_run() -> None:
    run, checkpoint, target_run, _, target_agent, session = _records()
    source_agent_id = target_run.origin_agent_id
    assert source_agent_id is not None
    gateway_message_id = uuid.uuid4()
    checkpoint.state["snapshots"] = RunInputSnapshots(
        session_context={},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "gateway_message_id": str(gateway_message_id),
            "gateway_reply_agent_id": str(source_agent_id),
        },
    )
    target_run.parent_run_id = None
    target_run.root_run_id = None
    inbound = GatewayMessage(
        id=gateway_message_id,
        agent_id=target_agent.id,
        sender_agent_id=source_agent_id,
        content="Research the facts",
        status="delivered",
        conversation_id=str(session.id),
    )
    db = _Session(
        target_run,
        target_agent,
        session,
        None,
        records={(GatewayMessage, gateway_message_id): inbound},
    )
    participant = SimpleNamespace(id=uuid.uuid4())

    with (
        patch(
            "app.services.agent_runtime.a2a_completion.get_or_create_agent_participant",
            new=AsyncMock(return_value=participant),
        ),
        patch(
            "app.services.agent_runtime.a2a_completion.RuntimeCommandIntake.resume_run",
            new=AsyncMock(),
        ) as resume_run,
    ):
        await A2ARuntimeCompletionHandler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        ).handle(run=run, checkpoint=checkpoint)

    resume_run.assert_not_awaited()
    assert inbound.status == "completed"
    assert inbound.result == "Verified research result"
    chat_message = next(value for value in db.added if isinstance(value, ChatMessage))
    assert chat_message.content == "Verified research result"
    assert chat_message.participant_id == participant.id
    reply = next(value for value in db.added if isinstance(value, GatewayMessage))
    assert reply.id == uuid.uuid5(
        run.run_id,
        "gateway-a2a-terminal:target-terminal",
    )
    assert reply.agent_id == source_agent_id
    assert reply.sender_agent_id == target_agent.id
    assert reply.status == "pending"


@pytest.mark.asyncio
async def test_notify_projects_target_result_without_resuming_source() -> None:
    run, checkpoint, target_run, source_run, target_agent, session = _records(
        mode="notify"
    )
    db = _Session(target_run, None, source_run, target_agent, session)

    with (
        patch(
            "app.services.agent_runtime.a2a_completion.get_or_create_agent_participant",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ),
        patch(
            "app.services.agent_runtime.a2a_completion.RuntimeCommandIntake.resume_run",
            new=AsyncMock(),
        ) as resume_run,
    ):
        await A2ARuntimeCompletionHandler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        ).handle(run=run, checkpoint=checkpoint)

    resume_run.assert_not_awaited()
    assert isinstance(db.added[0], ChatMessage)
    assert db.added[0].content == "Verified research result"


@pytest.mark.asyncio
async def test_existing_terminal_message_makes_callback_idempotent() -> None:
    run, checkpoint, target_run, _, _, _ = _records()
    receipt_id = uuid.uuid5(run.run_id, "a2a-terminal:target-terminal")
    db = _Session(target_run, receipt_id)
    factory = _SessionFactory(db)

    with patch(
        "app.services.agent_runtime.a2a_completion.RuntimeCommandIntake.resume_run",
        new=AsyncMock(),
    ) as resume_run:
        await A2ARuntimeCompletionHandler(
            session_factory=factory,  # type: ignore[arg-type]
        ).handle(run=run, checkpoint=checkpoint)

    resume_run.assert_not_awaited()
    assert db.added == []
    assert db.flushes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("failed", "target_failed"), ("cancelled", "target_cancelled")],
)
async def test_unsuccessful_target_resumes_source_with_structured_error(
    status: str,
    expected_code: str,
) -> None:
    run, checkpoint, target_run, source_run, target_agent, session = _records(
        status=status
    )
    db = _Session(target_run, None, source_run, target_agent, session)
    handle = RunHandle(
        tenant_id=run.tenant_id,
        run_id=source_run.id,
        thread_id=str(source_run.id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with (
        patch(
            "app.services.agent_runtime.a2a_completion.get_or_create_agent_participant",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ),
        patch(
            "app.services.agent_runtime.a2a_completion.RuntimeCommandIntake.resume_run",
            new=AsyncMock(return_value=handle),
        ) as resume_run,
    ):
        await A2ARuntimeCompletionHandler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        ).handle(run=run, checkpoint=checkpoint)

    payload = resume_run.await_args.args[0].payload["payload"]
    assert payload["status"] == status
    assert payload["error"]["code"] == expected_code
    assert payload["result_summary"] is None
