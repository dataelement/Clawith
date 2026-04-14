"""Shared helper: find-or-create ChatSession by external channel conv_id.

Used by feishu.py, slack.py, discord_bot.py, wecom.py, teams.py — eliminates in-process caches.
"""
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession


async def find_or_create_channel_session(
    db: AsyncSession,
    agent_id: _uuid.UUID,
    user_id: _uuid.UUID,
    external_conv_id: str,
    source_channel: str,
    first_message_title: str,
    is_group: bool = False,
    group_name: str | None = None,
) -> ChatSession:
    """Find an existing ChatSession by (agent_id, external_conv_id), or create one.

    Relies on the UNIQUE constraint on (agent_id, external_conv_id) in the DB.

    Args:
        is_group: True for group chat sessions (Feishu group, Slack channel, etc.).
                  Group sessions keep user_id as the agent creator (placeholder) and
                  are excluded from the user's "mine" session list.
        group_name: Display name for group sessions (e.g. IM group/channel name).
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.agent_id == agent_id,
            ChatSession.external_conv_id == external_conv_id,
        )
    )
    session = result.scalar_one_or_none()

    if session is None:
        now = datetime.now(timezone.utc)
        session = ChatSession(
            agent_id=agent_id,
            user_id=user_id,
            title=group_name[:40] if (is_group and group_name) else first_message_title[:40],
            source_channel=source_channel,
            external_conv_id=external_conv_id,
            is_group=is_group,
            group_name=group_name,
            created_at=now,
        )
        db.add(session)
        await db.flush()  # populate session.id
    else:
        # For P2P sessions: re-attribute to the correct user
        # (fixes legacy sessions stored under creator_id)
        if not session.is_group and session.user_id != user_id:
            session.user_id = user_id

        # For group sessions: update group_name if it changed
        if session.is_group and group_name and session.group_name != group_name:
            session.group_name = group_name
            session.title = group_name[:40]

    return session
