"""Stable terminal/waiting attachment for non-Web chat adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import uuid

from sqlalchemy import select

from app.models.audit import ChatMessage
from app.services.agent_runtime.chat_stream import RuntimeEventSource
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.contracts import RunHandle
from app.services.agent_runtime.event_stream import DatabaseRuntimeEventStream


ChannelChatStatus = Literal["completed", "failed", "cancelled", "waiting_user"]


class ChannelChatRuntimeError(RuntimeError):
    """A channel cannot resolve the durable result for its accepted Run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChannelChatOutcome:
    status: ChannelChatStatus
    content: str
    message_id: uuid.UUID | None
    correlation_id: str | None = None


async def _message(
    session_factory: RuntimeSessionFactory,
    *,
    message_id: uuid.UUID,
    session_id: uuid.UUID,
) -> ChatMessage:
    async with session_factory() as db:
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == str(session_id),
            )
        )
        message = result.scalar_one_or_none()
    if message is None or message.role not in {"assistant", "system"}:
        raise ChannelChatRuntimeError(
            "channel_delivery_message_missing",
            "Runtime delivery does not resolve to the accepted channel session",
        )
    return message


async def wait_for_channel_chat(
    *,
    handle: RunHandle,
    session_id: uuid.UUID,
    session_factory: RuntimeSessionFactory,
    event_source: RuntimeEventSource | None = None,
) -> ChannelChatOutcome:
    """Wait for the first user-visible boundary of one channel Run attachment."""
    source = event_source or DatabaseRuntimeEventStream(session_factory=session_factory)
    lifecycle_status: ChannelChatStatus | None = None
    correlation_id: str | None = None

    async for event in source.stream_run(handle):
        payload = event.payload
        if event.event_type == "waiting_started" and payload.get("waiting_type") == "user":
            lifecycle_status = "waiting_user"
            raw_correlation = payload.get("correlation_id")
            correlation_id = (
                raw_correlation.strip()
                if isinstance(raw_correlation, str) and raw_correlation.strip()
                else None
            )
        elif event.event_type == "run_completed":
            lifecycle_status = "completed"
        elif event.event_type == "run_failed":
            lifecycle_status = "failed"
        elif event.event_type == "run_cancelled":
            lifecycle_status = "cancelled"
        if event.event_type not in {"delivery_succeeded", "delivery_failed"}:
            continue
        if payload.get("delivery_kind") not in {"waiting", "terminal"}:
            continue

        raw_status = payload.get("lifecycle_status")
        status = lifecycle_status or (
            raw_status
            if raw_status in {"completed", "failed", "cancelled", "waiting_user"}
            else None
        )
        if status is None:
            raise ChannelChatRuntimeError(
                "channel_delivery_without_lifecycle",
                "Runtime channel delivery has no lifecycle status",
            )
        if event.event_type == "delivery_failed":
            return ChannelChatOutcome(
                status=status,
                content="Runtime result could not be delivered to this channel session.",
                message_id=None,
                correlation_id=correlation_id,
            )
        try:
            message_id = uuid.UUID(str(payload.get("message_id")))
        except (TypeError, ValueError) as exc:
            raise ChannelChatRuntimeError(
                "invalid_channel_delivery_receipt",
                "Runtime channel delivery has no valid message ID",
            ) from exc
        message = await _message(
            session_factory,
            message_id=message_id,
            session_id=session_id,
        )
        return ChannelChatOutcome(
            status=status,
            content=message.content,
            message_id=message.id,
            correlation_id=correlation_id,
        )

    raise ChannelChatRuntimeError(
        "channel_runtime_ended_without_delivery",
        "Runtime event stream ended before a channel delivery boundary",
    )


__all__ = [
    "ChannelChatOutcome",
    "ChannelChatRuntimeError",
    "ChannelChatStatus",
    "wait_for_channel_chat",
]
