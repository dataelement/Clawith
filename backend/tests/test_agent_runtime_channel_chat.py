"""External channel attachment tests for stable Runtime delivery events."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import uuid

import pytest

from app.models.audit import ChatMessage
from app.services.agent_runtime.channel_chat import wait_for_channel_chat
from app.services.agent_runtime.contracts import RunHandle, RuntimeEvent


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

    async def stream_run(self, _handle, *, after=None):
        assert after is None
        for event in self.events:
            yield event


def _handle() -> RunHandle:
    run_id = uuid.uuid4()
    return RunHandle(
        tenant_id=uuid.uuid4(),
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )


def _event(
    handle: RunHandle,
    event_type: str,
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
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        + timedelta(seconds=position),
    )


@pytest.mark.asyncio
async def test_external_group_waiter_loads_the_persisted_terminal_message() -> None:
    handle = _handle()
    session_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        user_id=None,
        role="assistant",
        content="Channel result",
        conversation_id=str(session_id),
        mentions=[],
    )
    events = [
        _event(handle, "run_completed", 1, {"status": "completed"}),
        _event(
            handle,
            "delivery_succeeded",
            2,
            {
                "delivery_kind": "terminal",
                "lifecycle_status": "completed",
                "message_id": str(message.id),
            },
        ),
    ]

    outcome = await wait_for_channel_chat(
        handle=handle,
        session_id=session_id,
        session_factory=_SessionFactory(_Session(message)),  # type: ignore[arg-type]
        event_source=_EventSource(events),
    )

    assert outcome.status == "completed"
    assert outcome.content == "Channel result"
    assert outcome.message_id == message.id


@pytest.mark.asyncio
async def test_channel_waiter_returns_waiting_correlation_for_next_message() -> None:
    handle = _handle()
    session_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        role="assistant",
        content="Approve publishing?",
        conversation_id=str(session_id),
        mentions=[],
    )
    events = [
        _event(
            handle,
            "waiting_started",
            1,
            {
                "status": "waiting_user",
                "waiting_type": "user",
                "correlation_id": "publish-1",
            },
        ),
        _event(
            handle,
            "delivery_succeeded",
            2,
            {
                "delivery_kind": "waiting",
                "lifecycle_status": "waiting_user",
                "message_id": str(message.id),
            },
        ),
    ]

    outcome = await wait_for_channel_chat(
        handle=handle,
        session_id=session_id,
        session_factory=_SessionFactory(_Session(message)),  # type: ignore[arg-type]
        event_source=_EventSource(events),
    )

    assert outcome.status == "waiting_user"
    assert outcome.correlation_id == "publish-1"
