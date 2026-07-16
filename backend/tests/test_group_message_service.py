"""Atomic group message and single-Agent mention intake tests."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.models.user import User
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.model_capabilities import (
    PlatformModelConfigurationError,
)
from app.services.group_message_service import (
    GroupMessageServiceError,
    ResolvedGroupMention,
    _SenderScope,
    _dedupe_mentions,
    _resolve_mentions,
    enqueue_group_message,
    list_group_messages,
)


NOW = datetime(2026, 7, 14, 11, 0, tzinfo=UTC)


class _ScalarCollection:
    def __init__(self, values=()) -> None:
        self.values = list(values)

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _Session:
    def __init__(self, *, existing_message: ChatMessage | None = None, results=()) -> None:
        self.existing_message = existing_message
        self.results = deque(results)
        self.added = []
        self.flushes = 0
        self.statements = []

    async def get(self, model, identity):
        if model is ChatMessage and self.existing_message is not None:
            assert identity == self.existing_message.id
            return self.existing_message
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.popleft()

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=False,
        AGENT_RUNTIME_V2_SOURCE_TYPES="chat",
    )


def _records():
    tenant_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Ada",
        role="member",
        is_active=True,
    )
    sender = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user.id,
        display_name=user.display_name,
    )
    group = Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Runtime Group",
        created_by_participant_id=sender.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group.id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=sender.id,
        title="Session 1",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_at=NOW,
        updated_at=NOW,
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
        access_mode="company",
    )
    target = Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=agent.id,
        display_name=agent.name,
    )
    scope = _SenderScope(
        group=group,
        session=session,
        participant=sender,
        user_id=user.id,
        agent_id=None,
        role="user",
    )
    mention = ResolvedGroupMention(
        participant_id=target.id,
        participant_type="agent",
        participant_ref_id=agent.id,
        display_name=agent.name,
        valid=True,
        triggers_agent=True,
        agent=agent,
        model=model,
    )
    return tenant_id, user, scope, target, mention


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


def test_mentions_are_deduplicated_in_client_order() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()

    assert _dedupe_mentions([first, second, first, second]) == (first, second)


@pytest.mark.asyncio
async def test_mention_resolution_only_exposes_active_group_members() -> None:
    tenant_id, user, scope, target, mention = _records()
    human_target = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user.id,
        display_name=user.display_name,
    )
    outsider = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=uuid.uuid4(),
        display_name="Other Tenant User",
    )
    memberships = [
        GroupMember(
            id=uuid.uuid4(),
            group_id=scope.group.id,
            participant_id=target.id,
            role="member",
            joined_at=NOW,
            session_read_state={},
        ),
        GroupMember(
            id=uuid.uuid4(),
            group_id=scope.group.id,
            participant_id=human_target.id,
            role="member",
            joined_at=NOW,
            session_read_state={},
        ),
    ]
    db = _Session(
        results=(
            _ScalarCollection([target, human_target, outsider]),
            _ScalarCollection(memberships),
            _ScalarCollection([user]),
            _ScalarCollection([mention.agent]),
            _ScalarCollection([mention.model]),
        )
    )

    resolved = await _resolve_mentions(
        db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        group_id=scope.group.id,
        participant_ids=(target.id, human_target.id, outsider.id),
    )

    assert resolved[0].valid is True and resolved[0].triggers_agent is True
    assert resolved[0].agent is mention.agent
    assert resolved[1].valid is True and resolved[1].triggers_agent is False
    assert resolved[1].participant_type == "user"
    assert resolved[2].valid is False
    assert resolved[2].reason == "not_group_member"
    assert resolved[2].display_name is None


@pytest.mark.asyncio
async def test_public_message_and_single_mention_start_share_one_session() -> None:
    tenant_id, user, scope, target, mention = _records()
    db = _Session()
    message_id = uuid.uuid4()
    handle = _handle(tenant_id)

    with (
        patch(
            "app.services.group_message_service._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.group_message_service._resolve_mentions",
            new=AsyncMock(return_value=(mention,)),
        ),
        patch(
            "app.services.group_message_service.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        intake = await enqueue_group_message(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            sender_participant_id=scope.participant.id,
            content="Please analyze the launch plan",
            mention_participant_ids=[target.id, target.id],
            message_id=message_id,
            settings_override=_settings(),
            clock=NOW,
        )

    assert intake.created is True
    assert intake.dispatch_kind == "single"
    assert intake.run_handles == (handle,)
    assert len(db.added) == 1
    message = db.added[0]
    assert isinstance(message, ChatMessage)
    assert message.id == message_id
    assert message.created_at == NOW
    assert message.participant_id == scope.participant.id
    assert message.user_id == user.id
    assert message.conversation_id == str(scope.session.id)
    assert message.mentions == [mention.payload()]
    assert scope.session.last_message_at == NOW
    assert scope.session.title == "Please analyze the launch plan"

    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.source_execution_id == (
        f"group_mention:{message_id}:agent:{mention.agent.id}"
    )
    assert command.source_type == "chat"
    assert command.run_kind == "foreground"
    assert command.model_id == mention.model.id
    assert command.session_id == scope.session.id
    assert command.scheduling_lane_key == f"group_mention:{tenant_id}:{mention.agent.id}"
    assert command.scheduling_position_created_at == NOW
    assert command.scheduling_position_id == message_id
    assert command.delivery_target == {
        "kind": "group",
        "session_id": str(scope.session.id),
        "group_id": str(scope.group.id),
    }
    assert command.origin_user_id == user.id
    assert command.payload["target_participant_id"] == str(target.id)
    assert command.payload["context_cutoff"] == {
        "message_id": str(message_id),
        "created_at": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_multi_agent_message_creates_one_planning_root_in_the_same_transaction() -> None:
    tenant_id, _, scope, target, mention = _records()
    other_agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Writer",
        primary_model_id=mention.model.id,
        status="idle",
        is_expired=False,
        access_mode="company",
    )
    other_target_id = uuid.uuid4()
    other = ResolvedGroupMention(
        participant_id=other_target_id,
        participant_type="agent",
        participant_ref_id=other_agent.id,
        display_name=other_agent.name,
        valid=True,
        triggers_agent=True,
        agent=other_agent,
        model=mention.model,
    )
    db = _Session()
    handle = _handle(tenant_id)

    with (
        patch(
            "app.services.group_message_service._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.group_message_service._resolve_mentions",
            new=AsyncMock(return_value=(mention, other)),
        ),
        patch(
            "app.services.group_message_service.resolve_multi_agent_planning_model",
            new=AsyncMock(return_value=mention.model),
        ),
        patch(
            "app.services.group_message_service.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        intake = await enqueue_group_message(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            sender_participant_id=scope.participant.id,
            content="Work together",
            mention_participant_ids=[target.id, other_target_id],
            settings_override=_settings(),
            clock=NOW,
        )

    assert intake.dispatch_kind == "planning"
    assert intake.run_handles == (handle,)
    assert intake.error_code is None
    assert len(db.added) == 1
    command = start_run.await_args.args[0]
    assert command.run_kind == "orchestration"
    assert command.system_role == "group_planning"
    assert command.agent_id is None
    assert command.source_execution_id == f"group_mention:{intake.message.id}:plan"
    assert command.scheduling_lane_key is None
    assert command.payload["context_cutoff"] == {
        "message_id": str(intake.message.id),
        "created_at": NOW.isoformat(),
    }
    assert command.payload["candidate_agents"] == [
        {
            "agent_id": str(mention.agent.id),
            "participant_id": str(mention.participant_id),
            "name": mention.agent.name,
            "role_description": mention.agent.role_description or "",
        },
        {
            "agent_id": str(other.agent.id),
            "participant_id": str(other.participant_id),
            "name": other.agent.name,
            "role_description": other.agent.role_description or "",
        },
    ]


@pytest.mark.asyncio
async def test_missing_planning_model_persists_one_visible_idempotent_failure() -> None:
    tenant_id, _, scope, target, mention = _records()
    other_agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Writer",
        primary_model_id=mention.model.id,
        status="idle",
        is_expired=False,
        access_mode="company",
    )
    other = ResolvedGroupMention(
        participant_id=uuid.uuid4(),
        participant_type="agent",
        participant_ref_id=other_agent.id,
        display_name=other_agent.name,
        valid=True,
        triggers_agent=True,
        agent=other_agent,
        model=mention.model,
    )
    db = _Session()

    with (
        patch(
            "app.services.group_message_service._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.group_message_service._resolve_mentions",
            new=AsyncMock(return_value=(mention, other)),
        ),
        patch(
            "app.services.group_message_service.resolve_multi_agent_planning_model",
            new=AsyncMock(
                side_effect=PlatformModelConfigurationError(
                    "MULTI_AGENT_PLANNING_MODEL_ID",
                    "is not configured",
                )
            ),
        ),
        patch(
            "app.services.group_message_service.RuntimeCommandIntake.start_run",
            new=AsyncMock(),
        ) as start_run,
    ):
        intake = await enqueue_group_message(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            sender_participant_id=scope.participant.id,
            content="Work together",
            mention_participant_ids=[target.id, other.participant_id],
            settings_override=_settings(),
            clock=NOW,
        )

    assert intake.dispatch_kind == "planning"
    assert intake.run_handles == ()
    assert intake.error_code == "planning_model_unavailable"
    start_run.assert_not_awaited()
    assert len(db.added) == 2
    public_message, failure_message = db.added
    assert isinstance(public_message, ChatMessage)
    assert isinstance(failure_message, ChatMessage)
    assert intake.new_public_messages == (public_message, failure_message)
    assert failure_message.id == uuid.uuid5(
        public_message.id,
        "planning-configuration-failure",
    )
    assert failure_message.role == "system"
    assert failure_message.participant_id is None
    assert failure_message.content == "任务规划未完成，请重试或改为单 Agent 处理。"
    assert failure_message.created_at == NOW.replace(microsecond=1)


@pytest.mark.asyncio
async def test_invalid_or_human_mentions_remain_public_without_starting_runtime() -> None:
    tenant_id, _, scope, target, _ = _records()
    human = ResolvedGroupMention(
        participant_id=target.id,
        participant_type="user",
        participant_ref_id=uuid.uuid4(),
        display_name="Grace",
        valid=True,
        triggers_agent=False,
    )
    invalid = ResolvedGroupMention(
        participant_id=uuid.uuid4(),
        participant_type=None,
        participant_ref_id=None,
        display_name=None,
        valid=False,
        triggers_agent=False,
        reason="not_group_member",
    )
    db = _Session()

    with (
        patch(
            "app.services.group_message_service._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.group_message_service._resolve_mentions",
            new=AsyncMock(return_value=(human, invalid)),
        ),
        patch(
            "app.services.group_message_service.RuntimeCommandIntake.start_run",
            new=AsyncMock(),
        ) as start_run,
    ):
        intake = await enqueue_group_message(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            sender_participant_id=scope.participant.id,
            content="FYI",
            mention_participant_ids=[human.participant_id, invalid.participant_id],
            settings_override=_settings(),
            clock=NOW,
        )

    assert intake.dispatch_kind == "none"
    assert intake.run_handles == ()
    start_run.assert_not_awaited()
    assert db.added[0].mentions == [human.payload(), invalid.payload()]


@pytest.mark.asyncio
async def test_message_forward_cursor_returns_newer_rows_in_position_order() -> None:
    tenant_id, _, scope, _, _ = _records()
    first = ChatMessage(
        id=uuid.uuid4(),
        role="user",
        content="first newer message",
        conversation_id=str(scope.session.id),
        participant_id=scope.participant.id,
        mentions=[],
        created_at=NOW,
    )
    second = ChatMessage(
        id=uuid.uuid4(),
        role="assistant",
        content="second newer message",
        conversation_id=str(scope.session.id),
        participant_id=scope.participant.id,
        mentions=[],
        created_at=NOW,
    )
    after = (NOW, uuid.uuid4())
    db = _Session(results=(_ScalarCollection([first, second]),))

    with patch(
        "app.services.group_message_service._load_sender_scope",
        new=AsyncMock(return_value=scope),
    ):
        messages = await list_group_messages(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            viewer_participant_id=scope.participant.id,
            limit=50,
            after=after,
        )

    assert messages == [first, second]
    sql = str(db.statements[0])
    assert "chat_messages.created_at, chat_messages.id) >" in sql
    assert "chat_messages.created_at ASC, chat_messages.id ASC" in sql


@pytest.mark.asyncio
async def test_message_cursors_are_mutually_exclusive() -> None:
    tenant_id, _, scope, _, _ = _records()
    cursor = (NOW, uuid.uuid4())
    db = _Session()

    with (
        patch(
            "app.services.group_message_service._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        pytest.raises(GroupMessageServiceError) as exc_info,
    ):
        await list_group_messages(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            group_id=scope.group.id,
            session_id=scope.session.id,
            viewer_participant_id=scope.participant.id,
            limit=50,
            before=cursor,
            after=cursor,
        )

    assert exc_info.value.code == "group_message_cursor_conflict"
    assert db.statements == []
