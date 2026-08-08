"""Durable scheduling tests for declared asynchronous Tool operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime import async_tool_poll


_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class _Scalars:
    def __init__(self, values: list[AgentToolExecution]) -> None:
        self._values = values

    def all(self) -> list[AgentToolExecution]:
        return list(self._values)


class _Result:
    def __init__(self, values: list[AgentToolExecution]) -> None:
        self._values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)


class _Session:
    def __init__(self, values: list[AgentToolExecution]) -> None:
        self.values = values
        self.flush_count = 0
        self.statements = []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.values)

    async def flush(self) -> None:
        self.flush_count += 1


def _factory(session: _Session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _pending_execution(*, due_at: datetime, scheduled: bool = False):
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    return AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id="launch-call",
        tool_name="arxiv_local-download_paper",
        assistant_message_id="assistant-1",
        arguments_hash="hash",
        effect="read",
        retry_policy="safe",
        status="started",
        result_metadata={
            "runtime_async_pending": True,
            "async_operation": {
                "version": 1,
                "operation_key": "operation-key",
                "operation_id": "2501.01234",
                "poll": {
                    "tool": "arxiv_local-download_paper",
                    "arguments": {
                        "paper_id": "2501.01234",
                        "check_status": True,
                    },
                    "interval_ms": 1000,
                },
            },
            "async_poll_due_at": due_at.isoformat(),
            "async_poll_correlation_id": "poll-correlation",
            "async_poll_call_id": "poll-call",
            "async_poll_scheduled": scheduled,
        },
    )


@pytest.mark.asyncio
async def test_due_async_poll_enqueues_one_idempotent_timer_resume(monkeypatch) -> None:
    execution = _pending_execution(due_at=_NOW - timedelta(seconds=1))
    session = _Session([execution])
    calls: list[dict] = []

    async def enqueue(db, **kwargs):
        assert db is session
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(async_tool_poll, "enqueue_resume", enqueue)
    scheduler = async_tool_poll.AsyncToolPollScheduler(
        session_factory=_factory(session),
        clock=lambda: _NOW,
    )

    result = await scheduler.run_once()

    assert result.status == "scheduled"
    assert result.execution_id == execution.id
    assert calls == [
        {
            "tenant_id": execution.tenant_id,
            "run_id": execution.run_id,
            "payload": {
                "resume_type": "timer",
                "correlation_id": execution.result_metadata[
                    "async_poll_correlation_id"
                ],
                "payload": {
                    "operation_key": "operation-key",
                    "tool_call_id": "launch-call",
                    "poll_call_id": "poll-call",
                    "poll": {
                        "tool": "arxiv_local-download_paper",
                        "arguments": {
                            "paper_id": "2501.01234",
                            "check_status": True,
                        },
                    },
                },
            },
            "idempotency_key": f"async-poll:{execution.id}",
        }
    ]
    assert execution.result_metadata["async_poll_scheduled"] is True
    assert session.flush_count == 1
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert "async_poll_scheduled" in compiled.params.values()
    assert "coalesce" in str(compiled).lower()


@pytest.mark.asyncio
async def test_future_or_already_scheduled_poll_is_not_enqueued(monkeypatch) -> None:
    future = _pending_execution(due_at=_NOW + timedelta(seconds=5))
    scheduled = _pending_execution(
        due_at=_NOW - timedelta(seconds=5),
        scheduled=True,
    )
    session = _Session([future, scheduled])

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"poll was enqueued early or twice: {args}, {kwargs}")

    monkeypatch.setattr(async_tool_poll, "enqueue_resume", forbidden)
    result = await async_tool_poll.AsyncToolPollScheduler(
        session_factory=_factory(session),
        clock=lambda: _NOW,
    ).run_once()

    assert result.status == "deferred"
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_legacy_pending_receipt_is_backfilled_and_resumed(monkeypatch) -> None:
    execution = _pending_execution(due_at=_NOW)
    legacy_metadata = dict(execution.result_metadata)
    for key in (
        "async_poll_due_at",
        "async_poll_correlation_id",
        "async_poll_call_id",
        "async_poll_scheduled",
    ):
        legacy_metadata.pop(key)
    execution.result_metadata = legacy_metadata
    execution.updated_at = _NOW - timedelta(seconds=2)
    session = _Session([execution])
    calls: list[dict] = []

    async def enqueue(_db, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(async_tool_poll, "enqueue_resume", enqueue)
    result = await async_tool_poll.AsyncToolPollScheduler(
        session_factory=_factory(session),
        clock=lambda: _NOW,
    ).run_once()

    assert result.status == "scheduled"
    assert calls[0]["payload"]["correlation_id"] == (
        f"tool-reconcile:{execution.run_id}"
    )
    assert calls[0]["payload"]["payload"]["poll_call_id"] == (
        f"async-poll:{execution.id}"
    )
    assert execution.result_metadata["async_poll_scheduled"] is True
