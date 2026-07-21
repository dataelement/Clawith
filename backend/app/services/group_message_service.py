"""Atomic native-group message intake and Runtime mention dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.models.user import User
from app.services.agent_runtime.adapter import (
    RuntimeAdapterError,
    RuntimeCommandIntake,
)
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.persistence import RuntimePersistenceError
from app.services.agent_runtime.model_capabilities import (
    PlatformModelConfigurationError,
    resolve_multi_agent_planning_model,
)


_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})
_MAX_CONTENT_LENGTH = 1_000_000
_MAX_MENTIONS = 100


class GroupMessageServiceError(RuntimeError):
    """A group message cannot be accepted without violating its durable contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedGroupMention:
    """One client mention token resolved without trusting display text."""

    participant_id: uuid.UUID
    participant_type: str | None
    participant_ref_id: uuid.UUID | None
    display_name: str | None
    valid: bool
    triggers_agent: bool
    reason: str | None = None
    agent: Agent | None = None
    model: LLMModel | None = None

    def payload(self) -> dict[str, object]:
        return {
            "participant_id": str(self.participant_id),
            "participant_type": self.participant_type,
            "participant_ref_id": (
                str(self.participant_ref_id) if self.participant_ref_id is not None else None
            ),
            "display_name": self.display_name,
            "valid": self.valid,
            "triggers_agent": self.triggers_agent,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class GroupMessageIntake:
    """The public message and durable work accepted in the caller transaction."""

    message: ChatMessage
    mentions: tuple[ResolvedGroupMention, ...]
    dispatch_kind: Literal["none", "single", "planning"]
    run_handles: tuple[RunHandle, ...]
    created: bool
    new_public_messages: tuple[ChatMessage, ...]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _SenderScope:
    group: Group
    session: ChatSession
    participant: Participant
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    role: Literal["user", "assistant"]


def _required_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise GroupMessageServiceError(
            "group_message_invalid",
            "Group message content must not be blank",
        )
    if len(content) > _MAX_CONTENT_LENGTH:
        raise GroupMessageServiceError(
            "group_message_invalid",
            f"Group message content exceeds {_MAX_CONTENT_LENGTH} characters",
        )
    return content


def _dedupe_mentions(participant_ids: list[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    if len(participant_ids) > _MAX_MENTIONS:
        raise GroupMessageServiceError(
            "group_mentions_invalid",
            f"A group message may contain at most {_MAX_MENTIONS} mention tokens",
        )
    return tuple(dict.fromkeys(participant_ids))


async def _load_sender_scope(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    sender_participant_id: uuid.UUID,
) -> _SenderScope:
    group_result = await db.execute(
        select(Group).where(
            Group.id == group_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
        )
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise GroupMessageServiceError("group_not_found", "Group not found")

    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == "group",
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise GroupMessageServiceError(
            "group_session_not_found",
            "Group session not found",
        )

    membership_result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id == sender_participant_id,
            GroupMember.removed_at.is_(None),
        )
    )
    if membership_result.scalar_one_or_none() is None:
        raise GroupMessageServiceError(
            "group_access_denied",
            "An active group membership is required to send a message",
        )

    participant_result = await db.execute(
        select(Participant).where(Participant.id == sender_participant_id)
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise GroupMessageServiceError(
            "group_sender_invalid",
            "Message sender participant does not exist",
        )
    if participant.type == "user":
        user_result = await db.execute(
            select(User).where(
                User.id == participant.ref_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        if user_result.scalar_one_or_none() is None:
            raise GroupMessageServiceError(
                "group_sender_invalid",
                "Message sender is not an active tenant user",
            )
        return _SenderScope(
            group=group,
            session=session,
            participant=participant,
            user_id=participant.ref_id,
            agent_id=None,
            role="user",
        )
    if participant.type == "agent":
        agent_result = await db.execute(
            select(Agent).where(
                Agent.id == participant.ref_id,
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
                Agent.access_mode != "private",
            )
        )
        if agent_result.scalar_one_or_none() is None:
            raise GroupMessageServiceError(
                "group_sender_invalid",
                "Message sender is not an available tenant Agent",
            )
        return _SenderScope(
            group=group,
            session=session,
            participant=participant,
            user_id=None,
            agent_id=participant.ref_id,
            role="assistant",
        )
    raise GroupMessageServiceError(
        "group_sender_invalid",
        "Message sender participant type is not supported",
    )


def _invalid_mention(
    participant_id: uuid.UUID,
    *,
    reason: str,
) -> ResolvedGroupMention:
    return ResolvedGroupMention(
        participant_id=participant_id,
        participant_type=None,
        participant_ref_id=None,
        display_name=None,
        valid=False,
        triggers_agent=False,
        reason=reason,
    )


async def _resolve_mentions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_ids: tuple[uuid.UUID, ...],
) -> tuple[ResolvedGroupMention, ...]:
    if not participant_ids:
        return ()

    participant_result = await db.execute(
        select(Participant).where(Participant.id.in_(participant_ids))
    )
    participants = {participant.id: participant for participant in participant_result.scalars().all()}
    membership_result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id.in_(participant_ids),
            GroupMember.removed_at.is_(None),
        )
    )
    active_member_ids = {
        membership.participant_id for membership in membership_result.scalars().all()
    }

    user_ref_ids = {
        participant.ref_id
        for participant_id, participant in participants.items()
        if participant_id in active_member_ids and participant.type == "user"
    }
    agent_ref_ids = {
        participant.ref_id
        for participant_id, participant in participants.items()
        if participant_id in active_member_ids and participant.type == "agent"
    }
    users: dict[uuid.UUID, User] = {}
    agents: dict[uuid.UUID, Agent] = {}
    models: dict[uuid.UUID, LLMModel] = {}
    if user_ref_ids:
        user_result = await db.execute(
            select(User).where(
                User.id.in_(user_ref_ids),
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        users = {user.id: user for user in user_result.scalars().all()}
    if agent_ref_ids:
        agent_result = await db.execute(
            select(Agent).where(
                Agent.id.in_(agent_ref_ids),
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
                Agent.access_mode != "private",
            )
        )
        agents = {agent.id: agent for agent in agent_result.scalars().all()}
        model_ids = {agent.primary_model_id for agent in agents.values() if agent.primary_model_id}
        if model_ids:
            model_result = await db.execute(
                select(LLMModel).where(
                    LLMModel.id.in_(model_ids),
                    LLMModel.enabled.is_(True),
                )
            )
            models = {
                model.id: model
                for model in model_result.scalars().all()
                if model.tenant_id in {None, tenant_id}
            }

    output: list[ResolvedGroupMention] = []
    for participant_id in participant_ids:
        participant = participants.get(participant_id)
        if participant is None:
            output.append(_invalid_mention(participant_id, reason="participant_missing"))
            continue
        if participant_id not in active_member_ids:
            output.append(_invalid_mention(participant_id, reason="not_group_member"))
            continue
        if participant.type == "user":
            user = users.get(participant.ref_id)
            if user is None:
                output.append(_invalid_mention(participant_id, reason="user_unavailable"))
                continue
            output.append(
                ResolvedGroupMention(
                    participant_id=participant.id,
                    participant_type="user",
                    participant_ref_id=user.id,
                    display_name=participant.display_name,
                    valid=True,
                    triggers_agent=False,
                )
            )
            continue
        if participant.type != "agent":
            output.append(_invalid_mention(participant_id, reason="participant_type_invalid"))
            continue
        agent = agents.get(participant.ref_id)
        if agent is None:
            output.append(_invalid_mention(participant_id, reason="agent_unavailable"))
            continue
        model = models.get(agent.primary_model_id) if agent.primary_model_id is not None else None
        if model is None:
            output.append(_invalid_mention(participant_id, reason="agent_model_unavailable"))
            continue
        output.append(
            ResolvedGroupMention(
                participant_id=participant.id,
                participant_type="agent",
                participant_ref_id=agent.id,
                display_name=participant.display_name,
                valid=True,
                triggers_agent=True,
                agent=agent,
                model=model,
            )
        )
    return tuple(output)


async def _persist_message(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    scope: _SenderScope,
    content: str,
    mentions: tuple[ResolvedGroupMention, ...],
    clock: datetime,
) -> tuple[ChatMessage, bool]:
    mention_payload = [mention.payload() for mention in mentions]
    existing = await db.get(ChatMessage, message_id)
    expected = {
        "agent_id": scope.agent_id,
        "user_id": scope.user_id,
        "role": scope.role,
        "content": content,
        "conversation_id": str(scope.session.id),
        "participant_id": scope.participant.id,
        "mentions": mention_payload,
    }
    if existing is not None:
        mismatched = [field for field, value in expected.items() if getattr(existing, field) != value]
        if mismatched:
            raise GroupMessageServiceError(
                "group_message_idempotency_mismatch",
                "Group message ID already exists with different immutable input: "
                + ", ".join(sorted(mismatched)),
            )
        if existing.created_at is None:
            raise GroupMessageServiceError(
                "group_message_position_invalid",
                "Existing group message has no authoritative position",
            )
        return existing, False

    message = ChatMessage(
        id=message_id,
        agent_id=scope.agent_id,
        user_id=scope.user_id,
        role=scope.role,
        content=content,
        conversation_id=str(scope.session.id),
        participant_id=scope.participant.id,
        mentions=mention_payload,
        created_at=clock,
    )
    db.add(message)
    scope.session.last_message_at = clock
    scope.session.updated_at = clock
    if scope.session.title.startswith("Session "):
        scope.session.title = content.strip()[:40] or scope.session.title
    await db.flush()
    return message, True


def _single_mention_command(
    *,
    tenant_id: uuid.UUID,
    scope: _SenderScope,
    message: ChatMessage,
    mentions: tuple[ResolvedGroupMention, ...],
    target: ResolvedGroupMention,
) -> StartRunCommand:
    if target.agent is None or target.model is None or message.created_at is None:
        raise GroupMessageServiceError(
            "group_mention_dispatch_invalid",
            "Resolved Agent mention is missing a pinned execution identity",
        )
    source_execution_id = f"group_mention:{message.id}:agent:{target.agent.id}"
    origin_user_id = scope.user_id
    origin_agent_id = scope.agent_id
    return StartRunCommand(
        tenant_id=tenant_id,
        agent_id=target.agent.id,
        session_id=scope.session.id,
        source_type="chat",
        source_id=str(message.id),
        source_execution_id=source_execution_id,
        goal=message.content,
        run_kind="foreground",
        model_id=target.model.id,
        scheduling_lane_key=f"group_mention:{tenant_id}:{target.agent.id}",
        scheduling_position_created_at=message.created_at,
        scheduling_position_id=message.id,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(scope.session.id),
            "group_id": str(scope.group.id),
        },
        idempotency_key=f"start:{source_execution_id}",
        payload={
            "message_id": str(message.id),
            "group_id": str(scope.group.id),
            "session_id": str(scope.session.id),
            "sender_participant_id": str(scope.participant.id),
            "mention_targets": [mention.payload() for mention in mentions],
            "target_participant_id": str(target.participant_id),
            "context_cutoff": {
                "message_id": str(message.id),
                "created_at": message.created_at.isoformat(),
            },
            "source_channel": scope.session.source_channel,
        },
        origin_user_id=origin_user_id,
        origin_agent_id=origin_agent_id,
        actor_user_id=origin_user_id,
        actor_agent_id=origin_agent_id,
    )


def _planning_command(
    *,
    tenant_id: uuid.UUID,
    scope: _SenderScope,
    message: ChatMessage,
    mentions: tuple[ResolvedGroupMention, ...],
    targets: tuple[ResolvedGroupMention, ...],
    model: LLMModel,
) -> StartRunCommand:
    if message.created_at is None:
        raise GroupMessageServiceError(
            "group_mention_dispatch_invalid",
            "Planning trigger message has no Message Position",
        )
    source_execution_id = f"group_mention:{message.id}:plan"
    return StartRunCommand(
        tenant_id=tenant_id,
        agent_id=None,
        session_id=scope.session.id,
        source_type="chat",
        source_id=str(message.id),
        source_execution_id=source_execution_id,
        goal=message.content,
        run_kind="orchestration",
        system_role="group_planning",
        model_id=model.id,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(scope.session.id),
            "group_id": str(scope.group.id),
        },
        idempotency_key=f"start:{source_execution_id}",
        payload={
            "message_id": str(message.id),
            "group_id": str(scope.group.id),
            "session_id": str(scope.session.id),
            "sender_participant_id": str(scope.participant.id),
            "mention_targets": [mention.payload() for mention in mentions],
            "context_cutoff": {
                "message_id": str(message.id),
                "created_at": message.created_at.isoformat(),
            },
            "candidate_agents": [
                {
                    "agent_id": str(target.agent.id),
                    "participant_id": str(target.participant_id),
                    "name": target.agent.name,
                    "role_description": target.agent.role_description or "",
                }
                for target in targets
                if target.agent is not None
            ],
            "source_channel": scope.session.source_channel,
        },
        origin_user_id=scope.user_id,
        origin_agent_id=scope.agent_id,
        actor_user_id=scope.user_id,
        actor_agent_id=scope.agent_id,
    )


async def _persist_planning_configuration_failure(
    db: AsyncSession,
    *,
    scope: _SenderScope,
    trigger_message: ChatMessage,
    clock: datetime,
) -> tuple[ChatMessage, bool]:
    message_id = uuid.uuid5(trigger_message.id, "planning-configuration-failure")
    existing = await db.get(ChatMessage, message_id)
    if existing is not None:
        return existing, False
    created_at = clock + timedelta(microseconds=1)
    message = ChatMessage(
        id=message_id,
        agent_id=None,
        user_id=None,
        role="system",
        content="任务规划未完成，请重试或改为单 Agent 处理。",
        conversation_id=str(scope.session.id),
        participant_id=None,
        mentions=[],
        created_at=created_at,
    )
    db.add(message)
    scope.session.last_message_at = created_at
    scope.session.updated_at = created_at
    await db.flush()
    return message, True


async def enqueue_group_message(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    sender_participant_id: uuid.UUID,
    content: str,
    mention_participant_ids: list[uuid.UUID] | None = None,
    message_id: uuid.UUID | None = None,
    settings_override: Settings | None = None,
    clock: datetime | None = None,
) -> GroupMessageIntake:
    """Persist one public message and any first Runtime command without committing."""
    normalized_content = _required_content(content)
    mention_ids = _dedupe_mentions(mention_participant_ids or [])
    scope = await _load_sender_scope(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        sender_participant_id=sender_participant_id,
    )
    mentions = await _resolve_mentions(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_ids=mention_ids,
    )
    agent_mentions = tuple(mention for mention in mentions if mention.triggers_agent)

    resolved_message_id = message_id or uuid.uuid4()
    message, created = await _persist_message(
        db,
        message_id=resolved_message_id,
        scope=scope,
        content=normalized_content,
        mentions=mentions,
        clock=clock or datetime.now(UTC),
    )
    if not agent_mentions:
        return GroupMessageIntake(
            message=message,
            mentions=mentions,
            dispatch_kind="none",
            run_handles=(),
            created=created,
            new_public_messages=(message,) if created else (),
        )

    runtime_settings = settings_override or get_settings()
    adapter = RuntimeCommandIntake(db, settings=runtime_settings)
    if len(agent_mentions) > 1:
        try:
            planning_model = await resolve_multi_agent_planning_model(
                db,
                runtime_settings,
                tenant_id=tenant_id,
            )
            handle = await adapter.start_run(
                _planning_command(
                    tenant_id=tenant_id,
                    scope=scope,
                    message=message,
                    mentions=mentions,
                    targets=agent_mentions,
                    model=planning_model,
                )
            )
        except (
            PlatformModelConfigurationError,
            RuntimeAdapterError,
            RuntimePersistenceError,
        ) as exc:
            failure_message, failure_created = await _persist_planning_configuration_failure(
                db,
                scope=scope,
                trigger_message=message,
                clock=message.created_at or datetime.now(UTC),
            )
            return GroupMessageIntake(
                message=message,
                mentions=mentions,
                dispatch_kind="planning",
                run_handles=(),
                created=created,
                new_public_messages=(
                    *((message,) if created else ()),
                    *((failure_message,) if failure_created else ()),
                ),
                error_code=(exc.code if hasattr(exc, "code") else "planning_model_unavailable"),
            )
        return GroupMessageIntake(
            message=message,
            mentions=mentions,
            dispatch_kind="planning",
            run_handles=(handle,),
            created=created,
            new_public_messages=(message,) if created else (),
        )

    try:
        handle = await adapter.start_run(
            _single_mention_command(
                tenant_id=tenant_id,
                scope=scope,
                message=message,
                mentions=mentions,
                target=agent_mentions[0],
            )
        )
    except (RuntimeAdapterError, RuntimePersistenceError) as exc:
        raise GroupMessageServiceError(exc.code, str(exc)) from exc
    return GroupMessageIntake(
        message=message,
        mentions=mentions,
        dispatch_kind="single",
        run_handles=(handle,),
        created=created,
        new_public_messages=(message,) if created else (),
    )


async def list_group_messages(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    viewer_participant_id: uuid.UUID,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> list[ChatMessage]:
    """Read public messages by the shared `(created_at, id)` position contract."""
    if before is not None and after is not None:
        raise GroupMessageServiceError(
            "group_message_cursor_conflict",
            "Message pagination accepts either `before` or `after`, not both",
        )
    await _load_sender_scope(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        sender_participant_id=viewer_participant_id,
    )
    if limit < 1 or limit > 500:
        raise GroupMessageServiceError(
            "group_message_limit_invalid",
            "Message limit must be between 1 and 500",
        )
    statement = select(ChatMessage).where(ChatMessage.conversation_id == str(session_id))
    if after is not None:
        statement = (
            statement.where(
                tuple_(ChatMessage.created_at, ChatMessage.id) > tuple_(after[0], after[1])
            )
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .limit(limit)
        )
        result = await db.execute(statement)
        return list(result.scalars().all())

    if before is not None:
        statement = statement.where(
            tuple_(ChatMessage.created_at, ChatMessage.id) < tuple_(before[0], before[1])
        )
    statement = statement.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit)
    result = await db.execute(statement)
    return list(reversed(result.scalars().all()))


__all__ = [
    "GroupMessageIntake",
    "GroupMessageServiceError",
    "ResolvedGroupMention",
    "enqueue_group_message",
    "list_group_messages",
]
