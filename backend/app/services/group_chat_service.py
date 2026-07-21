"""Transaction-scoped domain service for native group chats."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import build_visible_agents_query, can_use_agent
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services.chat_session_service import enqueue_session_deletion_cancels
from app.services.participant_identity import (
    get_or_create_agent_participant,
    get_or_create_user_participant,
)


_GROUP_SESSION_TYPE = "group"
_ACTIVE_AGENT_STATUSES = ("creating", "running", "idle")


class GroupChatServiceError(RuntimeError):
    """Stable domain failure raised before the caller transaction is committed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GroupSessionDeletion:
    """The group-session mutations staged in the caller transaction."""

    session: ChatSession
    replacement: ChatSession | None
    cancelled_run_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class GroupReadStateUpdate:
    """Result of a monotonic group-session read-watermark update."""

    membership: GroupMember
    session_id: uuid.UUID
    last_read_message_id: uuid.UUID
    advanced: bool


@dataclass(frozen=True, slots=True)
class GroupMemberCandidate:
    """One backend-authorized identity that can be invited by participant ID."""

    participant_id: uuid.UUID
    participant_type: str
    participant_ref_id: uuid.UUID
    display_name: str
    avatar_url: str | None
    role_description: str | None = None
    title: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _required_text(value: str, *, code: str, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise GroupChatServiceError(
            code,
            f"{field} must contain between 1 and {max_length} characters",
        )
    return normalized


async def _active_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    lock: bool = False,
) -> Group:
    statement = select(Group).where(
        Group.id == group_id,
        Group.tenant_id == tenant_id,
        Group.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    group = result.scalar_one_or_none()
    if group is None:
        raise GroupChatServiceError("group_not_found", "Group not found")
    return group


async def _valid_participant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    participant_id: uuid.UUID,
    human_only: bool,
    error_code: str,
) -> Participant:
    participant_result = await db.execute(select(Participant).where(Participant.id == participant_id))
    participant = participant_result.scalar_one_or_none()
    if participant is None or participant.type not in {"user", "agent"}:
        raise GroupChatServiceError(error_code, "Participant subject is not valid")

    if participant.type == "user":
        subject_result = await db.execute(
            select(User.id).where(
                User.id == participant.ref_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
    elif human_only:
        raise GroupChatServiceError(error_code, "An active human participant is required")
    else:
        subject_result = await db.execute(
            select(Agent).where(
                Agent.id == participant.ref_id,
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
            )
        )

    subject = subject_result.scalar_one_or_none()
    if subject is None:
        raise GroupChatServiceError(error_code, "Participant subject is not active in this tenant")
    if participant.type == "agent" and subject.access_mode == "private":
        raise GroupChatServiceError(error_code, "Private Agents cannot join a group")
    return participant


async def _active_membership(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
    lock: bool = False,
) -> GroupMember:
    statement = select(GroupMember).where(
        GroupMember.group_id == group_id,
        GroupMember.participant_id == participant_id,
        GroupMember.removed_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    membership = result.scalar_one_or_none()
    if membership is None:
        raise GroupChatServiceError("group_access_denied", "Active group membership is required")
    return membership


async def _human_actor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
    manager_only: bool,
    lock_membership: bool = False,
) -> tuple[GroupMember, Participant]:
    membership = await _active_membership(
        db,
        group_id=group_id,
        participant_id=participant_id,
        lock=lock_membership,
    )
    participant = await _valid_participant(
        db,
        tenant_id=tenant_id,
        participant_id=participant_id,
        human_only=True,
        error_code="group_human_member_required",
    )
    if manager_only and membership.role != "manager":
        raise GroupChatServiceError("group_manager_required", "Group manager permission is required")
    return membership, participant


async def authorize_group_member(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
    human_only: bool = False,
) -> tuple[Group, GroupMember, Participant]:
    """Resolve one active group member for group-scoped files and Runtime tools."""
    group = await _active_group(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
    )
    membership = await _active_membership(
        db,
        group_id=group_id,
        participant_id=participant_id,
    )
    participant = await _valid_participant(
        db,
        tenant_id=tenant_id,
        participant_id=participant_id,
        human_only=human_only,
        error_code=(
            "group_human_member_required" if human_only else "group_access_denied"
        ),
    )
    return group, membership, participant


async def _group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    lock: bool = False,
) -> ChatSession:
    statement = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.tenant_id == tenant_id,
        ChatSession.session_type == _GROUP_SESSION_TYPE,
        ChatSession.group_id == group_id,
        ChatSession.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    session = result.scalar_one_or_none()
    if session is None:
        raise GroupChatServiceError("group_session_not_found", "Group session not found")
    return session


async def authorize_group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    human_only: bool = False,
) -> ChatSession:
    """Return an active group session after validating its active viewer."""
    await authorize_group_member(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
        human_only=human_only,
    )
    return await _group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
    )


def _message_position(message: ChatMessage, *, error_code: str) -> tuple[datetime, int]:
    if message.created_at is None:
        raise GroupChatServiceError(error_code, "Message position is incomplete")
    return message.created_at, message.id.int


async def _session_message(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    error_code: str,
) -> ChatMessage:
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.conversation_id == str(session_id),
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise GroupChatServiceError(error_code, "Message is not part of this group session")
    return message


def _watermark_message_id(state: dict, session_id: uuid.UUID) -> uuid.UUID | None:
    entry = state.get(str(session_id))
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise GroupChatServiceError("group_read_state_invalid", "Session read state is invalid")
    raw_message_id = entry.get("last_read_message_id")
    if not isinstance(raw_message_id, str):
        raise GroupChatServiceError("group_read_state_invalid", "Session read watermark is invalid")
    try:
        return uuid.UUID(raw_message_id)
    except ValueError as exc:
        raise GroupChatServiceError(
            "group_read_state_invalid",
            "Session read watermark is invalid",
        ) from exc


async def create_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_participant_id: uuid.UUID,
    name: str,
    description: str | None = None,
) -> Group:
    """Create a group and its initial manager without owning the transaction."""
    normalized_name = _required_text(
        name,
        code="group_name_invalid",
        field="name",
        max_length=200,
    )
    await _valid_participant(
        db,
        tenant_id=tenant_id,
        participant_id=creator_participant_id,
        human_only=True,
        error_code="group_creator_invalid",
    )

    now = _now()
    group = Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=normalized_name,
        description=description,
        created_by_participant_id=creator_participant_id,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    creator_membership = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        participant_id=creator_participant_id,
        role="manager",
        joined_at=now,
        removed_at=None,
        session_read_state={},
    )
    db.add(group)
    db.add(creator_membership)
    await db.flush()
    return group


async def list_groups(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> list[Group]:
    """List active groups visible through the participant's active memberships."""
    result = await db.execute(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
            GroupMember.participant_id == participant_id,
            GroupMember.removed_at.is_(None),
        )
        .order_by(Group.updated_at.desc(), Group.id.desc())
    )
    return list(result.scalars().all())


async def get_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> Group:
    """Return an active group only when the participant is an active member."""
    result = await db.execute(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            Group.id == group_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
            GroupMember.participant_id == participant_id,
            GroupMember.removed_at.is_(None),
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise GroupChatServiceError("group_not_found", "Group not found")
    return group


async def update_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    update_description: bool = False,
) -> Group:
    """Update group metadata as an active human member."""
    group = await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=False,
    )

    changed = False
    if name is not None:
        group.name = _required_text(
            name,
            code="group_name_invalid",
            field="name",
            max_length=200,
        )
        changed = True
    if update_description:
        group.description = description
        changed = True
    if changed:
        group.updated_at = _now()
        await db.flush()
    return group


async def list_group_members(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> list[GroupMember]:
    """List active memberships for an active group member."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id)
    await _active_membership(
        db,
        group_id=group_id,
        participant_id=actor_participant_id,
    )
    result = await db.execute(
        select(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
        .order_by(GroupMember.joined_at, GroupMember.id)
    )
    return list(result.scalars().all())


async def list_group_member_candidates(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    actor_user: User,
    participant_type: str,
    limit: int,
) -> tuple[GroupMemberCandidate, ...]:
    """List inviteable identities while keeping participant IDs backend-owned."""
    if participant_type not in {"user", "agent"}:
        raise GroupChatServiceError(
            "group_participant_type_invalid",
            "Participant type must be 'user' or 'agent'",
        )

    await _active_group(db, tenant_id=tenant_id, group_id=group_id)
    _, actor = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=False,
    )
    if (
        actor.ref_id != actor_user.id
        or actor_user.tenant_id != tenant_id
        or not actor_user.is_active
    ):
        raise GroupChatServiceError(
            "group_human_member_required",
            "An active human group member is required",
        )

    active_refs_result = await db.execute(
        select(Participant.ref_id)
        .join(GroupMember, GroupMember.participant_id == Participant.id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
            Participant.type == participant_type,
        )
    )
    active_ref_ids = set(active_refs_result.scalars().all())

    candidates: list[GroupMemberCandidate] = []
    if participant_type == "user":
        statement = select(User).where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
        if active_ref_ids:
            statement = statement.where(User.id.not_in(active_ref_ids))
        result = await db.execute(
            statement.order_by(func.lower(User.display_name), User.id).limit(limit)
        )
        for user in result.scalars().all():
            participant = await get_or_create_user_participant(
                db,
                user.id,
                user.display_name,
                user.avatar_url,
            )
            candidates.append(
                GroupMemberCandidate(
                    participant_id=participant.id,
                    participant_type="user",
                    participant_ref_id=user.id,
                    display_name=user.display_name,
                    avatar_url=user.avatar_url,
                    title=user.title,
                )
            )
    else:
        statement = build_visible_agents_query(
            actor_user,
            tenant_id=tenant_id,
        ).where(
            Agent.access_mode != "private",
            Agent.status.in_(_ACTIVE_AGENT_STATUSES),
            Agent.is_expired.is_(False),
        )
        if active_ref_ids:
            statement = statement.where(Agent.id.not_in(active_ref_ids))
        result = await db.execute(
            statement.order_by(func.lower(Agent.name), Agent.id).limit(limit)
        )
        for agent in result.scalars().all():
            participant = await get_or_create_agent_participant(
                db,
                agent.id,
                agent.name,
                agent.avatar_url,
            )
            candidates.append(
                GroupMemberCandidate(
                    participant_id=participant.id,
                    participant_type="agent",
                    participant_ref_id=agent.id,
                    display_name=agent.name,
                    avatar_url=agent.avatar_url,
                    role_description=agent.role_description,
                )
            )

    return tuple(candidates)


async def invite_group_member(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> GroupMember:
    """Invite a valid tenant participant, reusing a removed membership row."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    _, actor = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=False,
    )
    target = await _valid_participant(
        db,
        tenant_id=tenant_id,
        participant_id=participant_id,
        human_only=False,
        error_code="group_participant_invalid",
    )
    if target.type == "agent":
        actor_user_result = await db.execute(
            select(User).where(
                User.id == actor.ref_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        actor_user = actor_user_result.scalar_one_or_none()
        target_agent_result = await db.execute(
            select(Agent).where(
                Agent.id == target.ref_id,
                Agent.tenant_id == tenant_id,
            )
        )
        target_agent = target_agent_result.scalar_one_or_none()
        if (
            actor_user is None
            or target_agent is None
            or not await can_use_agent(db, actor_user, target_agent)
        ):
            raise GroupChatServiceError(
                "group_participant_invalid",
                "Agent is not visible to the inviting member",
            )

    existing_result = await db.execute(
        select(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id == participant_id,
        )
        .with_for_update()
    )
    membership = existing_result.scalar_one_or_none()
    now = _now()
    if membership is not None:
        if membership.removed_at is None:
            raise GroupChatServiceError(
                "group_member_already_active",
                "Participant is already an active group member",
            )
        membership.role = "member"
        membership.joined_at = now
        membership.removed_at = None
        membership.session_read_state = {}
    else:
        membership = GroupMember(
            id=uuid.uuid4(),
            group_id=group_id,
            participant_id=participant_id,
            role="member",
            joined_at=now,
            removed_at=None,
            session_read_state={},
        )
        db.add(membership)
    await db.flush()
    return membership


async def remove_group_member(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    member_id: uuid.UUID,
) -> GroupMember:
    """Remove an active member while preserving a manager for a live group."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=True,
    )
    target_result = await db.execute(
        select(GroupMember)
        .where(
            GroupMember.id == member_id,
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
        .with_for_update()
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise GroupChatServiceError("group_member_not_found", "Active group member not found")

    if target.role == "manager":
        other_manager_result = await db.execute(
            select(GroupMember.id)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.id != target.id,
                GroupMember.role == "manager",
                GroupMember.removed_at.is_(None),
            )
            .limit(1)
        )
        if other_manager_result.scalar_one_or_none() is None:
            raise GroupChatServiceError(
                "group_last_manager_required",
                "A live group must retain at least one manager",
            )

    target.removed_at = _now()
    await db.flush()
    return target


async def create_group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """Create a group session; the first active session becomes primary."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=False,
    )
    existing_result = await db.execute(
        select(ChatSession.id)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == _GROUP_SESSION_TYPE,
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    is_primary = existing_result.scalar_one_or_none() is None
    now = _now()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type=_GROUP_SESSION_TYPE,
        group_id=group_id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=actor_participant_id,
        title=(
            _required_text(
                title,
                code="group_session_title_invalid",
                field="title",
                max_length=200,
            )
            if title is not None
            else f"Session {now.strftime('%m-%d %H:%M')}"
        ),
        source_channel="web",
        is_group=True,
        is_primary=is_primary,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.flush()
    return session


async def list_group_sessions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> list[ChatSession]:
    """List active sessions visible to an active group member."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id)
    await _active_membership(
        db,
        group_id=group_id,
        participant_id=actor_participant_id,
    )
    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == _GROUP_SESSION_TYPE,
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        )
        .order_by(ChatSession.created_at, ChatSession.id)
    )
    return list(result.scalars().all())


async def update_group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    title: str,
) -> ChatSession:
    """Rename a group session as an active human member."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=False,
    )
    session = await _group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        lock=True,
    )
    session.title = _required_text(
        title,
        code="group_session_title_invalid",
        field="title",
        max_length=200,
    )
    session.updated_at = _now()
    await db.flush()
    return session


async def soft_delete_group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> GroupSessionDeletion:
    """Delete one non-final session and repair primary selection atomically."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    _, actor = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=True,
    )
    session = await _group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        lock=True,
    )
    was_primary = bool(session.is_primary)
    now = _now()
    session.deleted_at = now
    session.is_primary = False
    session.updated_at = now
    await db.flush()

    replacement = None
    if was_primary:
        remaining_result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.session_type == _GROUP_SESSION_TYPE,
                ChatSession.group_id == group_id,
                ChatSession.id != session_id,
                ChatSession.deleted_at.is_(None),
            )
            .order_by(
                ChatSession.last_message_at.desc().nulls_last(),
                ChatSession.created_at.desc(),
                ChatSession.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        replacement = remaining_result.scalar_one_or_none()
        if replacement is not None:
            replacement.is_primary = True
            replacement.updated_at = now
            await db.flush()

    cancelled_run_ids = await enqueue_session_deletion_cancels(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        actor_user_id=actor.ref_id,
    )
    return GroupSessionDeletion(
        session=session,
        replacement=replacement,
        cancelled_run_ids=cancelled_run_ids,
    )


async def soft_delete_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> Group:
    """Disband a group, hide every session, and remove every active member."""
    group = await _active_group(db, tenant_id=tenant_id, group_id=group_id, lock=True)
    _, actor = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        manager_only=True,
    )
    session_result = await db.execute(
        select(ChatSession.id)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == _GROUP_SESSION_TYPE,
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        )
        .order_by(ChatSession.created_at, ChatSession.id)
    )
    session_ids = tuple(session_result.scalars().all())
    now = _now()
    group.deleted_at = now
    group.updated_at = now
    await db.execute(
        update(ChatSession)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == _GROUP_SESSION_TYPE,
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        )
        .values(deleted_at=now, is_primary=False, updated_at=now)
    )
    await db.execute(
        update(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
        .values(removed_at=now)
    )
    for session_id in session_ids:
        await enqueue_session_deletion_cancels(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            actor_user_id=actor.ref_id,
        )
    await db.flush()
    return group


async def mark_group_session_read(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    message_id: uuid.UUID,
) -> GroupReadStateUpdate:
    """Advance one human member's session watermark under a membership row lock."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id)
    membership, _ = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
        manager_only=False,
        lock_membership=True,
    )
    await _group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
    )
    new_message = await _session_message(
        db,
        session_id=session_id,
        message_id=message_id,
        error_code="group_message_not_found",
    )

    state = dict(membership.session_read_state or {})
    old_message_id = _watermark_message_id(state, session_id)
    if old_message_id is not None:
        old_message = await _session_message(
            db,
            session_id=session_id,
            message_id=old_message_id,
            error_code="group_read_state_invalid",
        )
        if _message_position(new_message, error_code="group_message_not_found") <= _message_position(
            old_message,
            error_code="group_read_state_invalid",
        ):
            return GroupReadStateUpdate(
                membership=membership,
                session_id=session_id,
                last_read_message_id=old_message_id,
                advanced=False,
            )

    state[str(session_id)] = {
        "last_read_message_id": str(message_id),
        "last_read_at": _now().isoformat(),
    }
    membership.session_read_state = state
    await db.flush()
    return GroupReadStateUpdate(
        membership=membership,
        session_id=session_id,
        last_read_message_id=message_id,
        advanced=True,
    )


async def get_group_session_unread_count(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> int:
    """Count public messages after the member's `(created_at, id)` watermark."""
    await _active_group(db, tenant_id=tenant_id, group_id=group_id)
    membership, _ = await _human_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
        manager_only=False,
    )
    await _group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
    )

    state = dict(membership.session_read_state or {})
    old_message_id = _watermark_message_id(state, session_id)
    position_filter = None
    if old_message_id is not None:
        old_message = await _session_message(
            db,
            session_id=session_id,
            message_id=old_message_id,
            error_code="group_read_state_invalid",
        )
        old_created_at, _ = _message_position(
            old_message,
            error_code="group_read_state_invalid",
        )
        position_filter = or_(
            ChatMessage.created_at > old_created_at,
            (ChatMessage.created_at == old_created_at) & (ChatMessage.id > old_message_id),
        )

    filters = [
        ChatMessage.conversation_id == str(session_id),
        ChatMessage.role.in_(("user", "assistant", "system")),
        or_(
            ChatMessage.participant_id.is_(None),
            ChatMessage.participant_id != participant_id,
        ),
    ]
    if position_filter is not None:
        filters.append(position_filter)
    result = await db.execute(select(func.count(ChatMessage.id)).where(*filters))
    return int(result.scalar_one() or 0)
