"""Stable AgentRunEvent streaming and reconnect cursor tests."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.services.agent_runtime.chat_stream import stream_web_chat_run
from app.services.agent_runtime.contracts import RunHandle, RuntimeEventCursor
from app.services.agent_runtime.event_stream import (
    DatabaseRuntimeEventStream,
    RuntimeEventStreamError,
)


class _Result:
    def __init__(self, *, scalar=None, rows=()) -> None:
        self.scalar = scalar
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, *results: _Result) -> None:
        self.results = deque(results)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.popleft()


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self) -> _Session:
        return self.sessions.popleft()


def _run() -> tuple[AgentRun, RunHandle]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        source_type="chat",
        goal="answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )
    return run, handle


def _direct_thread_run() -> tuple[AgentRun, RunHandle]:
    run, handle = _run()
    session_thread_id = str(uuid.uuid4())
    run.runtime_thread_id = session_thread_id
    return run, RunHandle(
        tenant_id=handle.tenant_id,
        run_id=handle.run_id,
        thread_id=session_thread_id,
        command_id=handle.command_id,
        runtime_type="langgraph",
        created=handle.created,
    )


def _event(
    run: AgentRun,
    event_type: str,
    *,
    created_at: datetime,
    checkpoint_id: str | None = "checkpoint-1",
) -> AgentRunEvent:
    return AgentRunEvent(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        agent_id=run.agent_id,
        event_type=event_type,
        summary=event_type.replace("_", " "),
        payload={"status": event_type},
        artifact_refs=["artifact://one"],
        idempotency_key=f"event:{event_type}",
        source_checkpoint_id=checkpoint_id,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_stream_yields_terminal_and_delivery_events_before_closing() -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
    terminal = _event(run, "run_completed", created_at=base)
    delivered = _event(
        run,
        "delivery_succeeded",
        created_at=base + timedelta(microseconds=1),
        checkpoint_id=None,
    )
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(
            _Result(rows=[terminal, delivered]),
            _Result(scalar="delivered"),
        ),
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    events = [event async for event in stream.stream_run(handle)]

    assert [event.event_type for event in events] == [
        "run_completed",
        "delivery_succeeded",
    ]
    assert events[0].event_id == terminal.id
    assert events[0].payload == {
        "status": "run_completed",
        "summary": "run completed",
        "artifact_refs": ["artifact://one"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_type", ("run_completed", "run_failed", "run_cancelled"))
@pytest.mark.parametrize(
    ("delivery_type", "delivery_status"),
    (("delivery_succeeded", "delivered"), ("delivery_failed", "failed")),
)
async def test_full_terminal_page_does_not_hide_persisted_delivery(
    terminal_type: str, delivery_type: str, delivery_status: str,
) -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
    rows = [
        _event(run, event_type, created_at=base + timedelta(microseconds=index))
        for index, event_type in enumerate(("status_changed", terminal_type, delivery_type))
    ]
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(_Result(rows=rows[:2]), _Result(scalar=delivery_status)),
        _Session(_Result(rows=rows[2:]), _Result(scalar=delivery_status)),
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
        batch_size=2,
    )

    events = [event async for event in stream.stream_run(handle)]

    assert [event.event_id for event in events] == [row.id for row in rows]
    assert not factory.sessions


@pytest.mark.asyncio
async def test_web_chat_receives_done_when_delivery_follows_full_terminal_page() -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, tzinfo=UTC)
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=run.agent_id,
        user_id=user_id,
        conversation_id=str(session_id),
        role="assistant",
        content="Finished result",
        mentions=[],
    )
    rows = [
        _event(run, event_type, created_at=base + timedelta(microseconds=index))
        for index, event_type in enumerate(("status_changed", "run_completed", "delivery_succeeded"))
    ]
    rows[-1].payload = {
        "delivery_kind": "terminal",
        "lifecycle_status": "completed",
        "message_id": str(message.id),
    }
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(_Result(rows=rows[:2]), _Result(scalar="delivered")),
        _Session(_Result(rows=rows[2:]), _Result(scalar="delivered")),
        _Session(_Result(scalar=message)),
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
        batch_size=2,
    )
    send = AsyncMock()

    outcome = await stream_web_chat_run(
        handle=handle,
        session_factory=factory,  # type: ignore[arg-type]
        send_packet=send,
        agent_id=run.agent_id,
        session_id=session_id,
        user_id=user_id,
        event_source=stream,
    )

    assert outcome.status == "completed"
    assert outcome.content == message.content
    assert outcome.cursor.event_id == rows[-1].id
    assert send.await_args.args[0]["type"] == "done"
    assert send.await_args.args[0]["message_id"] == str(message.id)
    assert not factory.sessions


@pytest.mark.asyncio
async def test_full_terminal_page_without_delivery_closes_after_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, handle = _run()
    terminal = _event(run, "run_completed", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(_Result(rows=[terminal]), _Result(scalar="not_required")),
        _Session(_Result(rows=[]), _Result(scalar="not_required")),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.agent_runtime.event_stream.asyncio.sleep", sleep)
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        batch_size=1,
    )

    events = [event async for event in stream.stream_run(handle)]

    assert [event.event_id for event in events] == [terminal.id]
    assert not factory.sessions
    sleep.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_backlog_pages_do_not_pay_idle_poll_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, tzinfo=UTC)
    rows = [
        _event(run, event_type, created_at=base + timedelta(microseconds=index))
        for index, event_type in enumerate(("run_created", "status_changed", "run_completed"))
    ]
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(_Result(rows=rows[:1]), _Result(scalar="pending")),
        _Session(_Result(rows=rows[1:2]), _Result(scalar="pending")),
        _Session(_Result(rows=rows[2:]), _Result(scalar="not_required")),
        _Session(_Result(rows=[]), _Result(scalar="not_required")),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.agent_runtime.event_stream.asyncio.sleep", sleep)
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        batch_size=1,
    )

    events = [event async for event in stream.stream_run(handle)]

    assert [event.event_id for event in events] == [row.id for row in rows]
    assert [call.args for call in sleep.await_args_list] == [(0,), (0,), (0,)]
    assert not factory.sessions


@pytest.mark.asyncio
async def test_terminal_projection_waits_for_later_delivery_settlement() -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
    terminal = _event(run, "run_failed", created_at=base)
    failed_delivery = _event(
        run,
        "delivery_failed",
        created_at=base + timedelta(seconds=1),
        checkpoint_id=None,
    )
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(_Result(rows=[terminal]), _Result(scalar="pending")),
        _Session(_Result(rows=[failed_delivery]), _Result(scalar="failed")),
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    events = [event async for event in stream.stream_run(handle)]

    assert [event.event_type for event in events] == ["run_failed", "delivery_failed"]


@pytest.mark.asyncio
async def test_reconnect_cursor_uses_created_at_and_id_together() -> None:
    run, handle = _run()
    base = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
    cursor = RuntimeEventCursor(base, uuid.uuid4())
    terminal = _event(run, "run_completed", created_at=base)
    poll = _Session(
        _Result(rows=[terminal]),
        _Result(scalar="not_required"),
    )
    factory = _SessionFactory(_Session(_Result(scalar=run)), poll)
    stream = DatabaseRuntimeEventStream(
        session_factory=factory,  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    events = [event async for event in stream.stream_run(handle, after=cursor)]

    assert len(events) == 1
    compiled = poll.statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)
    assert "agent_run_events.created_at >" in sql
    assert "agent_run_events.created_at =" in sql
    assert "agent_run_events.id >" in sql
    assert "ORDER BY agent_run_events.created_at ASC, agent_run_events.id ASC" in sql


@pytest.mark.asyncio
async def test_invalid_handle_is_rejected_before_database_access() -> None:
    run, handle = _run()
    del run
    invalid = RunHandle(
        tenant_id=handle.tenant_id,
        run_id=handle.run_id,
        thread_id="",
        command_id=handle.command_id,
        runtime_type="langgraph",
        created=handle.created,
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    with pytest.raises(RuntimeEventStreamError) as exc_info:
        await anext(stream.stream_run(invalid))

    assert exc_info.value.code == "runtime_identity_mismatch"


@pytest.mark.asyncio
async def test_direct_session_thread_handle_is_valid_even_when_thread_differs_from_run_id() -> None:
    run, handle = _direct_thread_run()
    base = datetime(2026, 7, 16, 18, 0, tzinfo=UTC)
    terminal = _event(run, "run_completed", created_at=base)
    delivered = _event(
        run,
        "delivery_succeeded",
        created_at=base + timedelta(microseconds=1),
        checkpoint_id=None,
    )
    factory = _SessionFactory(
        _Session(_Result(scalar=run)),
        _Session(
            _Result(rows=[terminal, delivered]),
            _Result(scalar="delivered"),
        ),
    )

    events = [
        event
        async for event in DatabaseRuntimeEventStream(
            session_factory=factory,  # type: ignore[arg-type]
            poll_interval_seconds=0.001,
        ).stream_run(handle)
    ]

    assert [event.event_type for event in events] == [
        "run_completed",
        "delivery_succeeded",
    ]


@pytest.mark.asyncio
async def test_event_stream_rejects_handle_thread_that_disagrees_with_stored_run() -> None:
    run, handle = _direct_thread_run()
    wrong = RunHandle(
        tenant_id=handle.tenant_id,
        run_id=handle.run_id,
        thread_id="wrong-thread",
        command_id=handle.command_id,
        runtime_type="langgraph",
        created=handle.created,
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=_SessionFactory(_Session(_Result(scalar=run))),  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    with pytest.raises(RuntimeEventStreamError) as exc_info:
        await anext(stream.stream_run(wrong))

    assert exc_info.value.code == "runtime_identity_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_identity", ("tenant", "run"))
async def test_event_stream_rejects_handle_outside_stored_tenant_run_scope(
    wrong_identity: str,
) -> None:
    _run_record, handle = _direct_thread_run()
    invalid = RunHandle(
        tenant_id=(uuid.uuid4() if wrong_identity == "tenant" else handle.tenant_id),
        run_id=(uuid.uuid4() if wrong_identity == "run" else handle.run_id),
        thread_id=handle.thread_id,
        command_id=handle.command_id,
        runtime_type="langgraph",
        created=handle.created,
    )
    stream = DatabaseRuntimeEventStream(
        session_factory=_SessionFactory(_Session(_Result(scalar=None))),  # type: ignore[arg-type]
        poll_interval_seconds=0.001,
    )

    with pytest.raises(RuntimeEventStreamError) as exc_info:
        await anext(stream.stream_run(invalid))

    assert exc_info.value.code == "run_not_found"
