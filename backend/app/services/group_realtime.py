"""Post-commit realtime notifications for native group messages."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.participant import Participant


def group_connection_key(group_id: uuid.UUID) -> str:
    """Namespace native Group sockets away from Agent connection keys."""
    return f"group:{group_id}"


def group_message_payload(message: ChatMessage, *, sender_name: str | None) -> dict:
    """Serialize the canonical GroupMessageOut-compatible websocket payload."""
    if message.created_at is None:
        raise ValueError("group realtime messages require a created_at position")
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "participant_id": str(message.participant_id) if message.participant_id else None,
        "sender_name": sender_name,
        "mentions": list(message.mentions or []),
        "created_at": message.created_at.isoformat(),
        "cursor": f"{message.created_at.isoformat()}|{message.id}",
    }


async def publish_group_message_created(
    *,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    message: dict,
) -> bool:
    """Broadcast one already-committed public message to Group members."""
    # Imported lazily so the service stays usable while the websocket module is
    # initializing. The existing manager supplies local delivery plus Redis fanout.
    from app.api.websocket import manager

    try:
        await manager.send_message(
            group_connection_key(group_id),
            {
                "type": "message.created",
                "group_id": str(group_id),
                "session_id": str(session_id),
                "message": message,
            },
        )
    except Exception as exc:
        # The durable cursor backfill is authoritative. A transient push outage
        # must not turn an already-committed message into an HTTP/Runtime failure.
        logger.warning(f"[GroupRealtime] message.created publish failed: {exc}")
        return False
    return True


async def publish_stored_group_message(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
) -> bool:
    """Load and broadcast a committed Runtime delivery, if its target is native Group chat."""
    async with session_factory() as db:
        session_result = await db.execute(
            select(ChatSession)
            .join(Group, Group.id == ChatSession.group_id)
            .where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.session_type == "group",
                ChatSession.group_id.is_not(None),
                ChatSession.deleted_at.is_(None),
                Group.tenant_id == tenant_id,
                Group.deleted_at.is_(None),
            )
        )
        session = session_result.scalar_one_or_none()
        if session is None or session.group_id is None:
            return False

        message_result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == str(session_id),
            )
        )
        message = message_result.scalar_one_or_none()
        if message is None:
            return False

        sender_name = None
        if message.participant_id is not None:
            participant_result = await db.execute(
                select(Participant.display_name).where(
                    Participant.id == message.participant_id
                )
            )
            sender_name = participant_result.scalar_one_or_none()

        payload = group_message_payload(message, sender_name=sender_name)
        group_id = session.group_id

    return await publish_group_message_created(
        group_id=group_id,
        session_id=session_id,
        message=payload,
    )


__all__ = [
    "group_connection_key",
    "group_message_payload",
    "publish_group_message_created",
    "publish_stored_group_message",
]
