"""Helpers for first-party chat session selection and creation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, cast, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession


async def get_primary_platform_session(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSession | None:
    """Return the current primary first-party session for a user+agent pair, if any."""

    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == user_id,
            ChatSession.source_channel == "web",
            ChatSession.is_group == False,
            ChatSession.is_primary == True,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_primary_platform_session(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSession:
    """Return a guaranteed primary platform session for a given user+agent pair.

    The upgrade strategy is intentionally lazy:
    - Reuse the existing primary session when it exists.
    - Otherwise promote the most relevant existing web session.
    - Only create a brand new primary session when the pair has never talked on-platform.
    """

    primary = await get_primary_platform_session(db, agent_id, user_id)
    if primary:
        return primary

    # Prefer a session with at least one user-authored message so we anchor the long-lived
    # primary conversation to the user's real historical thread when possible.
    user_message_count = (
        select(
            ChatMessage.conversation_id.label("conversation_id"),
            func.sum(case((ChatMessage.role == "user", 1), else_=0)).label("user_msg_count"),
        )
        .group_by(ChatMessage.conversation_id)
        .subquery()
    )

    result = await db.execute(
        select(ChatSession)
        .outerjoin(user_message_count, user_message_count.c.conversation_id == cast(ChatSession.id, String))
        .where(
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == user_id,
            ChatSession.source_channel == "web",
            ChatSession.is_group == False,
        )
        .order_by(
            case((func.coalesce(user_message_count.c.user_msg_count, 0) > 0, 0), else_=1),
            ChatSession.last_message_at.desc().nulls_last(),
            ChatSession.created_at.desc(),
        )
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.is_primary = True
        await db.flush()
        return existing

    now = datetime.now(timezone.utc)
    session = ChatSession(
        agent_id=agent_id,
        user_id=user_id,
        title=f"Session {now.strftime('%m-%d %H:%M')}",
        source_channel="web",
        is_primary=True,
        created_at=now,
    )
    db.add(session)
    await db.flush()
    return session
