"""Coalesced provisional answer observation tests."""

from __future__ import annotations

import asyncio
import uuid
from typing import Self

import pytest
from sqlalchemy.dialects import postgresql

from app.services.agent_runtime.answer_stream import AnswerStreamWriter


class _Transaction:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> Self:
        self._session.transaction_entries += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._session.transaction_exits += 1
        return False


class _Session:
    def __init__(self, *, fail_execute: bool = False) -> None:
        self.statements: list[object] = []
        self.transaction_entries = 0
        self.transaction_exits = 0
        self.fail_execute = fail_execute

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, statement) -> None:
        self.statements.append(statement)
        if self.fail_execute:
            raise RuntimeError("database unavailable")


class _SessionFactory:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.sessions: list[_Session] = []
        self.fail_first = fail_first

    def __call__(self) -> _Session:
        session = _Session(fail_execute=self.fail_first and not self.sessions)
        self.sessions.append(session)
        return session


def _params(statement: object) -> dict[str, object]:
    compiled = statement.compile(dialect=postgresql.dialect())
    return compiled.params


@pytest.mark.asyncio
async def test_close_coalesces_visible_deltas_into_one_tenant_scoped_event() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    attempt_id = uuid.uuid5(run_id, "model-step:2:primary:0")
    sessions = _SessionFactory()
    writer = AnswerStreamWriter(
        session_factory=sessions,
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        attempt_id=attempt_id,
        flush_interval=60,
        max_buffer_chars=100,
    )

    await writer.write("Hello")
    await writer.write(" world")

    assert sessions.sessions == []

    await writer.close()

    assert len(sessions.sessions) == 1
    session = sessions.sessions[0]
    assert session.transaction_entries == session.transaction_exits == 1
    assert len(session.statements) == 1
    statement = session.statements[0]
    params = _params(statement)
    assert params["tenant_id"] == tenant_id
    assert params["run_id"] == run_id
    assert params["agent_id"] == agent_id
    assert params["event_type"] == "status_changed"
    assert params["summary"] == "Assistant answer streaming"
    assert params["payload"] == {
        "activity_type": "assistant_delta",
        "status": "running",
        "attempt_id": str(attempt_id),
        "sequence": 1,
        "content": "Hello world",
        "reset": True,
    }
    assert params["artifact_refs"] == []
    assert params["idempotency_key"] == f"answer-stream:{attempt_id}:1"
    assert params["source_checkpoint_id"] is None
    assert params["id"] == uuid.uuid5(
        run_id,
        f"answer-stream-event:{attempt_id}:1",
    )
    assert "reasoning" not in str(params).lower()
    assert "tool" not in str(params).lower()


@pytest.mark.asyncio
async def test_size_and_interval_flushes_are_ordered_without_blocking_write() -> None:
    run_id = uuid.uuid4()
    attempt_id = uuid.uuid5(run_id, "model-step:1:primary:0")
    sessions = _SessionFactory()
    writer = AnswerStreamWriter(
        session_factory=sessions,
        tenant_id=uuid.uuid4(),
        run_id=run_id,
        agent_id=uuid.uuid4(),
        attempt_id=attempt_id,
        flush_interval=0.01,
        max_buffer_chars=4,
    )

    await writer.write("ABCD")
    assert sessions.sessions == []
    await asyncio.sleep(0.02)

    await writer.write("E")
    await asyncio.sleep(0.02)
    await writer.close()

    assert len(sessions.sessions) == 2
    first = _params(sessions.sessions[0].statements[0])
    second = _params(sessions.sessions[1].statements[0])
    assert first["payload"]["content"] == "ABCD"
    assert first["payload"]["sequence"] == 1
    assert first["payload"]["reset"] is True
    assert second["payload"]["content"] == "E"
    assert second["payload"]["sequence"] == 2
    assert second["payload"]["reset"] is False
    assert first["idempotency_key"] == f"answer-stream:{attempt_id}:1"
    assert second["idempotency_key"] == f"answer-stream:{attempt_id}:2"


@pytest.mark.asyncio
async def test_empty_deltas_are_ignored_and_closed_writer_rejects_more_content() -> None:
    sessions = _SessionFactory()
    writer = AnswerStreamWriter(
        session_factory=sessions,
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
    )

    await writer.write("")
    await writer.close()

    assert sessions.sessions == []
    with pytest.raises(RuntimeError, match="closed"):
        await writer.write("late")


@pytest.mark.asyncio
async def test_failed_flush_retries_the_same_sequence_and_content() -> None:
    run_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    sessions = _SessionFactory(fail_first=True)
    writer = AnswerStreamWriter(
        session_factory=sessions,
        tenant_id=uuid.uuid4(),
        run_id=run_id,
        agent_id=uuid.uuid4(),
        attempt_id=attempt_id,
        flush_interval=60,
    )

    await writer.write("recover me")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await writer.flush()

    assert writer.visible_started is False
    await writer.flush()
    await writer.close()

    assert len(sessions.sessions) == 2
    failed = _params(sessions.sessions[0].statements[0])
    retried = _params(sessions.sessions[1].statements[0])
    assert failed["id"] == retried["id"]
    assert failed["idempotency_key"] == retried["idempotency_key"]
    assert failed["payload"] == retried["payload"]
    assert writer.visible_started is True
