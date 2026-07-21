"""Map stable Runtime events back to the existing Web Chat packet contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol
import uuid

from sqlalchemy import select

from app.models.audit import ChatMessage
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.contracts import (
    RunHandle,
    RuntimeEvent,
    RuntimeEventCursor,
)
from app.services.agent_runtime.event_stream import DatabaseRuntimeEventStream


ChatStreamStatus = Literal["completed", "failed", "cancelled", "waiting_user"]
PacketSender = Callable[[dict], Awaitable[None]]


class RuntimeEventSource(Protocol):
    def stream_run(
        self,
        handle: RunHandle,
        *,
        after: RuntimeEventCursor | None = None,
    ) -> AsyncIterator[RuntimeEvent]: ...


class ChatRuntimeStreamError(RuntimeError):
    """A stable Runtime event cannot be mapped to the requested Web session."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChatRuntimeStreamOutcome:
    """The user-visible boundary reached by one stream attachment."""

    status: ChatStreamStatus
    content: str
    cursor: RuntimeEventCursor
    correlation_id: str | None = None


async def _load_delivered_message(
    session_factory: RuntimeSessionFactory,
    *,
    message_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatMessage:
    async with session_factory() as db:
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.agent_id == agent_id,
                ChatMessage.user_id == user_id,
                ChatMessage.conversation_id == str(session_id),
            )
        )
        message = result.scalar_one_or_none()
    if message is None or message.role not in {"assistant", "system"}:
        raise ChatRuntimeStreamError(
            "runtime_delivery_message_missing",
            "Runtime delivery receipt does not resolve to this Web Chat session",
        )
    return message


def _cursor(event: RuntimeEvent) -> RuntimeEventCursor:
    if event.created_at is None or event.event_id is None:
        raise ChatRuntimeStreamError(
            "invalid_runtime_event_position",
            "Runtime event has no stable reconnect position",
        )
    return RuntimeEventCursor(event.created_at, event.event_id)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


async def stream_web_chat_run(
    *,
    handle: RunHandle,
    session_factory: RuntimeSessionFactory,
    send_packet: PacketSender,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    after: RuntimeEventCursor | None = None,
    event_source: RuntimeEventSource | None = None,
) -> ChatRuntimeStreamOutcome:
    """Stream one start/resume attachment until terminal or waiting-user delivery."""
    source = event_source or DatabaseRuntimeEventStream(session_factory=session_factory)
    terminal_status: ChatStreamStatus | None = None
    waiting_correlation_id: str | None = None
    latest_cursor = after

    async for event in source.stream_run(handle, after=after):
        latest_cursor = _cursor(event)
        payload = event.payload

        activity_type = payload.get("activity_type")
        packet_position = {
            "run_id": str(handle.run_id),
            "event_id": str(event.event_id),
            "event_cursor": f"{event.created_at.isoformat()}|{event.event_id}",
        }
        if event.event_type == "status_changed" and activity_type == "thinking":
            content = _text(payload.get("content"))
            if content is not None:
                await send_packet({"type": "thinking", "content": content, **packet_position})
            continue
        if event.event_type == "status_changed" and activity_type == "assistant_progress":
            content = _text(payload.get("content"))
            if content is not None:
                await send_packet({"type": "chunk", "content": content, **packet_position})
            continue
        if event.event_type == "status_changed" and activity_type == "tool_call":
            tool_name = _text(payload.get("name"))
            call_id = _text(payload.get("call_id"))
            tool_status = payload.get("status")
            if tool_name is not None and call_id is not None and tool_status in {"running", "done"}:
                await send_packet(
                    {
                        "type": "tool_call",
                        "name": tool_name,
                        "call_id": call_id,
                        "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                        "status": tool_status,
                        "result": str(payload.get("result") or ""),
                        "reasoning_content": str(payload.get("reasoning_content") or ""),
                        "execution_status": payload.get("execution_status"),
                        "error_code": payload.get("error_code"),
                        **packet_position,
                    }
                )
            continue

        if event.event_type == "waiting_started" and payload.get("waiting_type") == "user":
            waiting_correlation_id = _text(payload.get("correlation_id"))
            if waiting_correlation_id is None:
                raise ChatRuntimeStreamError(
                    "runtime_wait_correlation_missing",
                    "waiting_user Runtime event has no resume correlation",
                )
            terminal_status = "waiting_user"
        elif event.event_type == "resumed":
            waiting_correlation_id = None
            terminal_status = None
        elif event.event_type == "run_completed":
            terminal_status = "completed"
        elif event.event_type == "run_failed":
            terminal_status = "failed"
        elif event.event_type == "run_cancelled":
            terminal_status = "cancelled"

        if event.event_type not in {"delivery_succeeded", "delivery_failed"}:
            await send_packet(
                {
                    "type": "runtime_status",
                    "run_id": str(handle.run_id),
                    "event": event.event_type,
                    "status": payload.get("status"),
                }
            )
            continue

        delivery_kind = payload.get("delivery_kind")
        if delivery_kind not in {"waiting", "terminal"}:
            continue
        if latest_cursor is None:
            raise ChatRuntimeStreamError(
                "invalid_runtime_event_position",
                "Runtime delivery has no reconnect position",
            )

        receipt_status = payload.get("lifecycle_status")
        if receipt_status not in {None, "waiting_user", "completed", "failed", "cancelled"}:
            raise ChatRuntimeStreamError(
                "invalid_runtime_delivery_receipt",
                "Runtime delivery receipt has an invalid lifecycle status",
            )
        status = terminal_status or receipt_status
        if delivery_kind == "waiting":
            status = "waiting_user"
            waiting_correlation_id = waiting_correlation_id or _text(
                payload.get("correlation_id")
            )
            if waiting_correlation_id is None:
                raise ChatRuntimeStreamError(
                    "runtime_wait_correlation_missing",
                    "waiting_user delivery has no resume correlation",
                )
        if status is None:
            raise ChatRuntimeStreamError(
                "runtime_delivery_without_lifecycle",
                "Runtime delivery arrived without its lifecycle event",
            )

        if event.event_type == "delivery_failed":
            content = "Runtime result could not be delivered to this chat."
            await send_packet(
                {
                    "type": "done",
                    "role": "assistant",
                    "content": content,
                    "run_id": str(handle.run_id),
                    "runtime_status": status,
                    "delivery_error": payload.get("error_code"),
                }
            )
            return ChatRuntimeStreamOutcome(
                status=status,
                content=content,
                cursor=latest_cursor,
                correlation_id=waiting_correlation_id,
            )

        raw_message_id = payload.get("message_id")
        try:
            message_id = uuid.UUID(str(raw_message_id))
        except (TypeError, ValueError) as exc:
            raise ChatRuntimeStreamError(
                "invalid_runtime_delivery_receipt",
                "Runtime delivery receipt has no valid message ID",
            ) from exc
        message = await _load_delivered_message(
            session_factory,
            message_id=message_id,
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
        )
        packet = {
            "type": "done",
            "role": "assistant",
            "content": message.content,
            "message_id": str(message.id),
            "run_id": str(handle.run_id),
            "runtime_status": status,
        }
        if waiting_correlation_id is not None:
            packet["correlation_id"] = waiting_correlation_id
        await send_packet(packet)
        return ChatRuntimeStreamOutcome(
            status=status,
            content=message.content,
            cursor=latest_cursor,
            correlation_id=waiting_correlation_id,
        )

    raise ChatRuntimeStreamError(
        "runtime_stream_ended_without_delivery",
        "Runtime event stream ended before a Web Chat delivery boundary",
    )


__all__ = [
    "ChatRuntimeStreamError",
    "ChatRuntimeStreamOutcome",
    "RuntimeEventSource",
    "stream_web_chat_run",
]
