"""Stable Runtime event mapping tests for the Web Chat compatibility protocol."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import uuid

import pytest

from app.models.audit import ChatMessage
from app.services.agent_runtime.chat_stream import stream_web_chat_run
from app.services.agent_runtime.contracts import (
    RunHandle,
    RuntimeEvent,
    RuntimeEventCursor,
)


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, message: ChatMessage) -> None:
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(self.message)


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self):
        return self.sessions.popleft()


class _EventSource:
    def __init__(self, events: list[RuntimeEvent]) -> None:
        self.events = events
        self.after: RuntimeEventCursor | None = None

    async def stream_run(self, handle, *, after=None):
        del handle
        self.after = after
        for event in self.events:
            yield event


def _handle() -> RunHandle:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    return RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )


def _event(
    handle: RunHandle,
    event_type: str,
    *,
    position: int,
    payload: dict,
) -> RuntimeEvent:
    return RuntimeEvent(
        tenant_id=handle.tenant_id,
        run_id=handle.run_id,
        event_id=uuid.uuid4(),
        event_type=event_type,  # type: ignore[arg-type]
        payload=payload,
        checkpoint_id=f"checkpoint-{position}",
        created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC) + timedelta(seconds=position),
    )


@pytest.mark.asyncio
async def test_completed_delivery_maps_to_existing_done_packet() -> None:
    handle = _handle()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        role="assistant",
        content="Finished result",
        conversation_id=str(session_id),
        mentions=[],
    )
    events = [
        _event(handle, "run_created", position=1, payload={"status": "running"}),
        _event(handle, "run_completed", position=2, payload={"status": "completed"}),
        _event(
            handle,
            "delivery_succeeded",
            position=3,
            payload={
                "delivery_kind": "terminal",
                "lifecycle_status": "completed",
                "message_id": str(message.id),
            },
        ),
    ]
    packets: list[dict] = []

    async def send(packet: dict) -> None:
        packets.append(packet)

    outcome = await stream_web_chat_run(
        handle=handle,
        session_factory=_SessionFactory(_Session(message)),  # type: ignore[arg-type]
        send_packet=send,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        event_source=_EventSource(events),
    )

    assert outcome.status == "completed"
    assert outcome.content == "Finished result"
    assert outcome.cursor.event_id == events[-1].event_id
    assert [packet["type"] for packet in packets] == [
        "runtime_status",
        "runtime_status",
        "done",
    ]
    assert packets[-1] == {
        "type": "done",
        "role": "assistant",
        "content": "Finished result",
        "message_id": str(message.id),
        "run_id": str(handle.run_id),
        "runtime_status": "completed",
    }


@pytest.mark.asyncio
async def test_runtime_observation_events_restore_thinking_and_tool_packets() -> None:
    handle = _handle()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        role="assistant",
        content="Finished result",
        conversation_id=str(session_id),
        mentions=[],
    )
    events = [
        _event(
            handle,
            "status_changed",
            position=1,
            payload={
                "activity_type": "thinking",
                "status": "running",
                "content": "I should inspect the file.",
            },
        ),
        _event(
            handle,
            "status_changed",
            position=2,
            payload={
                "activity_type": "tool_call",
                "status": "running",
                "name": "read_file",
                "call_id": "call-1",
                "args": {"path": "README.md"},
                "reasoning_content": "I should inspect the file.",
            },
        ),
        _event(
            handle,
            "status_changed",
            position=3,
            payload={
                "activity_type": "tool_call",
                "status": "done",
                "name": "read_file",
                "call_id": "call-1",
                "args": {"path": "README.md"},
                "result": "contents",
                "reasoning_content": "I should inspect the file.",
                "execution_status": "succeeded",
            },
        ),
        _event(handle, "run_completed", position=4, payload={"status": "completed"}),
        _event(
            handle,
            "delivery_succeeded",
            position=5,
            payload={
                "delivery_kind": "terminal",
                "lifecycle_status": "completed",
                "message_id": str(message.id),
            },
        ),
    ]
    packets: list[dict] = []

    async def send(packet: dict) -> None:
        packets.append(packet)

    await stream_web_chat_run(
        handle=handle,
        session_factory=_SessionFactory(_Session(message)),  # type: ignore[arg-type]
        send_packet=send,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        event_source=_EventSource(events),
    )

    assert [packet["type"] for packet in packets] == [
        "thinking",
        "tool_call",
        "tool_call",
        "runtime_status",
        "done",
    ]
    assert packets[1]["call_id"] == packets[2]["call_id"] == "call-1"
    assert packets[1]["status"] == "running"
    assert packets[2]["status"] == "done"
    assert packets[2]["result"] == "contents"
    assert packets[2]["event_cursor"].endswith(f"|{events[2].event_id}")


@pytest.mark.asyncio
async def test_waiting_delivery_returns_resume_identity_and_honors_cursor() -> None:
    handle = _handle()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        role="assistant",
        content="Should I publish it?",
        conversation_id=str(session_id),
        mentions=[],
    )
    events = [
        _event(
            handle,
            "waiting_started",
            position=2,
            payload={
                "status": "waiting_user",
                "waiting_type": "user",
                "correlation_id": "publish-confirmation",
            },
        ),
        _event(
            handle,
            "delivery_succeeded",
            position=3,
            payload={
                "delivery_kind": "waiting",
                "lifecycle_status": "waiting_user",
                "correlation_id": "publish-confirmation",
                "message_id": str(message.id),
            },
        ),
    ]
    source = _EventSource(events)
    after = RuntimeEventCursor(
        datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        uuid.uuid4(),
    )
    packets: list[dict] = []

    async def send(packet: dict) -> None:
        packets.append(packet)

    outcome = await stream_web_chat_run(
        handle=handle,
        session_factory=_SessionFactory(_Session(message)),  # type: ignore[arg-type]
        send_packet=send,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        after=after,
        event_source=source,
    )

    assert source.after == after
    assert outcome.status == "waiting_user"
    assert outcome.correlation_id == "publish-confirmation"
    assert packets[-1]["run_id"] == str(handle.run_id)
    assert packets[-1]["correlation_id"] == "publish-confirmation"
    assert packets[-1]["runtime_status"] == "waiting_user"


@pytest.mark.asyncio
async def test_delivery_receipt_status_is_sufficient_after_reconnect_cursor() -> None:
    handle = _handle()
    event = _event(
        handle,
        "delivery_failed",
        position=4,
        payload={
            "delivery_kind": "terminal",
            "lifecycle_status": "cancelled",
            "error_code": "session_deleted",
        },
    )
    packets: list[dict] = []

    async def send(packet: dict) -> None:
        packets.append(packet)

    outcome = await stream_web_chat_run(
        handle=handle,
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        send_packet=send,
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        event_source=_EventSource([event]),
    )

    assert outcome.status == "cancelled"
    assert packets[-1]["delivery_error"] == "session_deleted"
