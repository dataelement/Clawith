"""Web Chat intake tests for atomic Runtime start and resume commands."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.agent_runtime.chat_intake import (
    ChatRuntimeIntakeError,
    enqueue_chat_runtime,
    stored_user_content,
)
from app.services.agent_runtime.contracts import (
    ResumeRunCommand,
    RunHandle,
    StartRunCommand,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *, existing_message: ChatMessage | None = None, results=()) -> None:
        self.existing_message = existing_message
        self.results = deque(results)
        self.added: list[object] = []
        self.flushes = 0

    async def get(self, model, identity):
        if model is ChatMessage and self.existing_message is not None:
            assert self.existing_message.id == identity
            return self.existing_message
        return None

    async def execute(self, _statement):
        return _ScalarResult(self.results.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=False,
        AGENT_RUNTIME_V2_SOURCE_TYPES="chat" if enabled else "",
    )


def _records() -> tuple[Agent, User, ChatSession, LLMModel]:
    tenant_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Ada",
        avatar_url="https://example.test/ada.png",
        role="member",
        is_active=True,
    )
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="gpt-test",
        api_key_encrypted="secret",
        label="Test",
        enabled=True,
    )
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=user.id,
        name="Analyst",
        primary_model_id=model.id,
        status="idle",
        is_expired=False,
        agent_type="native",
    )
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="direct",
        agent_id=agent.id,
        user_id=user.id,
        title="Session 1",
        source_channel="web",
        is_group=False,
        is_primary=True,
    )
    return agent, user, session, model


def _handle(tenant_id: uuid.UUID) -> RunHandle:
    run_id = uuid.uuid4()
    return RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )


@pytest.mark.asyncio
async def test_chat_message_and_start_command_share_the_caller_session() -> None:
    agent, user, session, model = _records()
    db = _Session()
    message_id = uuid.uuid4()
    participant = SimpleNamespace(id=uuid.uuid4())
    handle = _handle(agent.tenant_id)

    with (
        patch(
            "app.services.agent_runtime.chat_intake.get_or_create_user_participant",
            new=AsyncMock(return_value=participant),
        ),
        patch(
            "app.services.agent_runtime.chat_intake.TransactionalAgentRuntimeAdapter.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        result = await enqueue_chat_runtime(
            db,  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="raw question",
            display_content="Visible question",
            file_name="evidence.txt",
            runtime_instruction="  Begin the trusted onboarding flow.  ",
            onboarding_target_phase="  greeted  ",
            message_id=message_id,
            settings_override=_settings(enabled=True),
        )

    assert result is not None
    assert result.handle == handle
    assert result.message_id == message_id
    assert result.resumed is False
    assert db.flushes == 1
    assert len(db.added) == 1
    message = db.added[0]
    assert isinstance(message, ChatMessage)
    assert message.id == message_id
    assert message.content == "[file:evidence.txt]\nVisible question"
    assert message.participant_id == participant.id
    assert message.conversation_id == str(session.id)
    assert session.last_message_at is not None
    assert session.title == "[file:evidence.txt]\nVisible question"[:40]

    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.source_type == "chat"
    assert command.source_id == str(message_id)
    assert command.source_execution_id == f"chat:{message_id}"
    assert command.session_id == session.id
    assert command.model_id == model.id
    assert command.delivery_status == "pending"
    assert command.delivery_target == {
        "kind": "direct",
        "session_id": str(session.id),
        "user_id": str(user.id),
    }
    assert command.payload["message_id"] == str(message_id)
    assert command.payload["input_content"] == "raw question"
    assert command.payload["runtime_instruction"] == "Begin the trusted onboarding flow."
    assert command.payload["onboarding_target_phase"] == "greeted"
    assert command.actor_user_id == user.id


@pytest.mark.asyncio
async def test_external_group_chat_uses_unified_session_without_native_group_scope() -> None:
    agent, user, _direct_session, model = _records()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id,
        session_type="group",
        group_id=None,
        agent_id=agent.id,
        user_id=agent.creator_id,
        title="Feishu Group",
        source_channel="feishu",
        external_conv_id="feishu_group_oc_123",
        is_group=True,
        is_primary=False,
    )
    db = _Session()
    participant = SimpleNamespace(id=uuid.uuid4())
    handle = _handle(agent.tenant_id)

    with (
        patch(
            "app.services.agent_runtime.chat_intake.get_or_create_user_participant",
            new=AsyncMock(return_value=participant),
        ),
        patch(
            "app.services.agent_runtime.chat_intake.TransactionalAgentRuntimeAdapter.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        intake = await enqueue_chat_runtime(
            db,  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="[发送者: Ada] Review this update",
            source_channel="feishu",
            settings_override=_settings(enabled=True),
        )

    assert intake is not None
    message = db.added[0]
    assert isinstance(message, ChatMessage)
    assert message.agent_id is None
    assert message.user_id is None
    assert message.participant_id == participant.id
    command = start_run.await_args.args[0]
    assert command.delivery_target == {
        "kind": "session",
        "session_id": str(session.id),
    }
    assert command.payload["source_channel"] == "feishu"


@pytest.mark.asyncio
async def test_chat_resume_persists_explicit_correlation_with_the_user_message() -> None:
    agent, user, session, model = _records()
    run_id = uuid.uuid4()
    waiting_run = AgentRun(
        id=run_id,
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        session_id=session.id,
        source_type="chat",
        source_id=str(uuid.uuid4()),
        goal="Answer the user",
        run_kind="foreground",
        model_id=model.id,
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime",
        graph_version="v1",
        lane_held=False,
        delivery_status="delivered",
        origin_user_id=user.id,
    )
    waiting_event = AgentRunEvent(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id,
        run_id=run_id,
        agent_id=agent.id,
        event_type="waiting_started",
        summary="Waiting for user",
        payload={"correlation_id": "confirm-7"},
        artifact_refs=[],
        idempotency_key="waiting-1",
        created_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
    )
    db = _Session(results=(waiting_run, waiting_event))
    participant = SimpleNamespace(id=uuid.uuid4())
    handle = _handle(agent.tenant_id)
    message_id = uuid.uuid4()

    with (
        patch(
            "app.services.agent_runtime.chat_intake.get_or_create_user_participant",
            new=AsyncMock(return_value=participant),
        ),
        patch(
            "app.services.agent_runtime.chat_intake.TransactionalAgentRuntimeAdapter.resume_run",
            new=AsyncMock(return_value=handle),
        ) as resume_run,
    ):
        result = await enqueue_chat_runtime(
            db,  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="Yes, continue",
            message_id=message_id,
            resume_run_id=run_id,
            resume_correlation_id="confirm-7",
            settings_override=_settings(enabled=True),
        )

    assert result is not None and result.resumed is True
    assert result.stream_after is not None
    assert result.stream_after.event_id == waiting_event.id
    assert result.stream_after.created_at == waiting_event.created_at
    command = resume_run.await_args.args[0]
    assert isinstance(command, ResumeRunCommand)
    assert command.run_id == run_id
    assert command.idempotency_key == f"resume:chat:{message_id}"
    assert command.payload == {
        "resume_type": "user_input",
        "correlation_id": "confirm-7",
        "payload": {
            "message_id": str(message_id),
            "content": "Yes, continue",
        },
    }
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_disabled_chat_rollout_does_not_mutate_the_legacy_path() -> None:
    agent, user, session, model = _records()
    db = _Session()

    with patch(
        "app.services.agent_runtime.chat_intake.get_or_create_user_participant",
        new=AsyncMock(),
    ) as participant:
        result = await enqueue_chat_runtime(
            db,  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="legacy",
            settings_override=_settings(enabled=False),
        )

    assert result is None
    assert db.added == []
    assert db.flushes == 0
    participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_resume_requires_run_and_correlation_together() -> None:
    agent, user, session, model = _records()

    with pytest.raises(ChatRuntimeIntakeError) as raised:
        await enqueue_chat_runtime(
            _Session(),  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="continue",
            resume_run_id=uuid.uuid4(),
            settings_override=_settings(enabled=True),
        )

    assert raised.value.code == "incomplete_chat_resume"


def test_image_input_keeps_executable_content_in_the_durable_message() -> None:
    content = "[image_data:data:image/png;base64,abc]"
    assert stored_user_content(
        content,
        display_content="[image]",
        file_name="chart.png",
    ) == f"[file:chart.png]\n{content}"


@pytest.mark.asyncio
async def test_synthetic_input_starts_without_persisting_a_human_message() -> None:
    agent, user, session, model = _records()
    db = _Session()
    handle = _handle(agent.tenant_id)

    with patch(
        "app.services.agent_runtime.chat_intake.TransactionalAgentRuntimeAdapter.start_run",
        new=AsyncMock(return_value=handle),
    ) as start_run:
        result = await enqueue_chat_runtime(
            db,  # type: ignore[arg-type]
            agent=agent,
            user=user,
            session=session,
            model=model,
            content="Please begin onboarding.",
            persist_user_message=False,
            application_tools_enabled=False,
            settings_override=_settings(enabled=True),
        )

    assert result is not None
    assert db.added == []
    assert db.flushes == 0
    command = start_run.await_args.args[0]
    assert command.payload["input_content"] == "Please begin onboarding."
    assert command.payload["application_tools_enabled"] is False
