"""Notification service — unified entry point for sending in-app notifications."""

import uuid
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def send_notification(
    db: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
    *,
    agent_id: Optional[uuid.UUID] = None,
    type: str,
    title: str,
    body: str = "",
    link: Optional[str] = None,
    ref_id: Optional[uuid.UUID] = None,
    sender_name: Optional[str] = None,
) -> Notification:
    """Create and persist a notification for a user or an agent.

    Args:
        db: Database session.
        user_id: The user who should receive this notification (for human recipients).
        agent_id: The agent who should receive this notification (for agent recipients).
        type: Notification category (approval_pending, plaza_comment, mention, broadcast, etc.).
        title: Short summary shown in the notification list.
        body: Extended detail text.
        link: Frontend route path for click-through navigation.
        ref_id: ID of the related object (approval, comment, etc.).
        sender_name: Display name of the sender.
    """
    if not user_id and not agent_id:
        raise ValueError("Either user_id or agent_id must be provided")

    notif = Notification(
        user_id=user_id,
        agent_id=agent_id,
        type=type,
        title=title,
        body=body,
        link=link,
        ref_id=ref_id,
        sender_name=sender_name,
    )
    db.add(notif)
    await db.flush()
    recipient = f"user {user_id}" if user_id else f"agent {agent_id}"
    logger.info(f"Notification [{type}] sent to {recipient}: {title}")
    return notif

