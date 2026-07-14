"""Canonical public projection for one committed native-group message."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.participant import Participant


@dataclass(frozen=True, slots=True)
class GroupMessageProjection:
    """A committed message plus the routing scope needed for WebSocket fan-out."""

    tenant_id: uuid.UUID
    group_id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID
    active_participant_ids: tuple[uuid.UUID, ...]
    event: dict[str, object]


def build_group_message_payload(
    message: ChatMessage,
    sender_name: str | None,
) -> dict[str, object]:
    """Build the canonical JSON-safe ``GroupMessageOut`` payload."""
    if message.created_at is None:
        raise ValueError("Committed group message has no created_at position")
    cursor_created_at = message.created_at.isoformat()
    created_at = cursor_created_at.replace("+00:00", "Z")
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "participant_id": (str(message.participant_id) if message.participant_id is not None else None),
        "sender_name": sender_name,
        "mentions": list(message.mentions or []),
        "created_at": created_at,
        "cursor": f"{cursor_created_at}|{message.id}",
    }


def build_group_message_event(
    *,
    message: ChatMessage,
    session: ChatSession,
    sender_name: str | None,
) -> dict[str, object]:
    """Wrap the canonical message payload in its group routing envelope."""
    if session.group_id is None:
        raise ValueError("Committed group message session has no group_id")
    return {
        "type": "message.created",
        "group_id": str(session.group_id),
        "session_id": str(session.id),
        "message": build_group_message_payload(message, sender_name),
    }


async def load_committed_group_message_projection(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
) -> GroupMessageProjection | None:
    """Reload one active group message after its owner transaction committed."""
    result = await db.execute(
        select(ChatMessage, ChatSession, Participant.display_name)
        .join(
            ChatSession,
            ChatMessage.conversation_id == cast(ChatSession.id, String),
        )
        .join(
            Group,
            (Group.id == ChatSession.group_id) & (Group.tenant_id == ChatSession.tenant_id),
        )
        .outerjoin(Participant, Participant.id == ChatMessage.participant_id)
        .where(
            ChatMessage.id == message_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == "group",
            ChatSession.group_id.is_not(None),
            ChatSession.deleted_at.is_(None),
            Group.deleted_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    message, session, sender_name = row
    if session.group_id is None:
        return None
    participant_result = await db.execute(
        select(GroupMember.participant_id)
        .where(
            GroupMember.group_id == session.group_id,
            GroupMember.removed_at.is_(None),
        )
        .order_by(GroupMember.participant_id)
    )
    active_participant_ids = tuple(participant_result.scalars().all())
    return GroupMessageProjection(
        tenant_id=tenant_id,
        group_id=session.group_id,
        session_id=session.id,
        message_id=message.id,
        active_participant_ids=active_participant_ids,
        event=build_group_message_event(
            message=message,
            session=session,
            sender_name=sender_name,
        ),
    )


__all__ = [
    "GroupMessageProjection",
    "build_group_message_event",
    "build_group_message_payload",
    "load_committed_group_message_projection",
]
