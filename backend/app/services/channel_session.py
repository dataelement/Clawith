"""Tenant-safe external-channel ChatSession creation and reuse."""

import uuid as _uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services.chat_session_service import create_direct_session
from app.services.participant_identity import get_or_create_user_participant


class ChannelSessionError(RuntimeError):
    """An external conversation cannot be mapped into the unified chat scope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def find_or_create_channel_session(
    db: AsyncSession,
    agent_id: _uuid.UUID,
    user_id: _uuid.UUID,
    external_conv_id: str,
    source_channel: str,
    first_message_title: str,
    is_group: bool = False,
    group_name: str | None = None,
    created_by_user_id: _uuid.UUID | None = None,
) -> ChatSession:
    """Find an existing ChatSession by (agent_id, external_conv_id), or create one.

    Relies on the UNIQUE constraint on (agent_id, external_conv_id) in the DB.

    Args:
        is_group: True for group chat sessions (Feishu group, Slack channel, etc.).
                  Group sessions keep user_id as the agent creator (placeholder) and
                  are excluded from the user's "mine" session list.
        group_name: Display name for group sessions (e.g. IM group/channel name).
    """
    normalized_channel = source_channel.strip()
    normalized_external_id = external_conv_id.strip()
    if (
        not normalized_channel
        or not normalized_external_id
        or len(normalized_channel) > 20
        or len(normalized_external_id) > 200
    ):
        raise ChannelSessionError(
            "channel_identity_missing",
            "External channel and conversation ID are required",
        )

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.tenant_id is None:
        raise ChannelSessionError(
            "channel_agent_unavailable",
            "External channel Agent has no tenant scope",
        )
    actor_user_id = created_by_user_id or user_id
    user_result = await db.execute(
        select(User).where(
            User.id == actor_user_id,
            User.tenant_id == agent.tenant_id,
            User.is_active.is_(True),
        )
    )
    actor = user_result.scalar_one_or_none()
    if actor is None:
        raise ChannelSessionError(
            "channel_user_unavailable",
            "External channel sender is not an active tenant user",
        )
    if user_id != actor_user_id:
        owner_result = await db.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == agent.tenant_id,
                User.is_active.is_(True),
            )
        )
        if owner_result.scalar_one_or_none() is None:
            raise ChannelSessionError(
                "channel_owner_unavailable",
                "External channel session owner is not an active tenant user",
            )
    participant = await get_or_create_user_participant(
        db,
        actor.id,
        actor.display_name,
        actor.avatar_url,
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))").bindparams(
            scope=f"channel-session:{agent.tenant_id}:{agent.id}:{normalized_external_id}"
        )
    )

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.tenant_id == agent.tenant_id,
            ChatSession.agent_id == agent_id,
            ChatSession.external_conv_id == normalized_external_id,
        )
    )
    session = result.scalar_one_or_none()

    if session is None:
        if is_group:
            now = datetime.now(UTC)
            session = ChatSession(
                tenant_id=agent.tenant_id,
                session_type="group",
                group_id=None,
                agent_id=agent_id,
                user_id=user_id,
                created_by_participant_id=participant.id,
                title=(group_name or first_message_title)[:40],
                source_channel=normalized_channel,
                external_conv_id=normalized_external_id,
                is_group=True,
                group_name=group_name,
                is_primary=False,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            db.add(session)
            await db.flush()
        else:
            session = await create_direct_session(
                db,
                tenant_id=agent.tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                created_by_participant_id=participant.id,
                title=first_message_title[:40],
            )
            session.source_channel = normalized_channel
            session.external_conv_id = normalized_external_id
    else:
        expected_type = "group" if is_group else "direct"
        if (
            session.tenant_id != agent.tenant_id
            or session.session_type != expected_type
            or session.source_channel != normalized_channel
            or (is_group and session.group_id is not None)
        ):
            raise ChannelSessionError(
                "channel_session_scope_mismatch",
                "External conversation is already bound to a different chat scope",
            )
        if session.deleted_at is not None:
            session.deleted_at = None
            session.is_primary = False
        # For P2P sessions: re-attribute to the correct user
        # (fixes legacy sessions stored under creator_id)
        if session.session_type == "direct" and session.user_id != user_id:
            session.user_id = user_id
        if session.created_by_participant_id is None:
            session.created_by_participant_id = participant.id

        # For group sessions: update group_name if it changed
        if session.session_type == "group" and group_name and session.group_name != group_name:
            session.group_name = group_name
            session.title = group_name[:40]
        session.is_group = is_group
        session.updated_at = datetime.now(UTC)

    return session


__all__ = ["ChannelSessionError", "find_or_create_channel_session"]
