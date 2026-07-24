"""Regression coverage for automatic AgentSchedule consumption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.services.scheduler import _tick


class _Result:
    def __init__(self, *, rows: list[object] | None = None, value: object | None = None) -> None:
        self._rows = rows
        self._value = value

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return list(self._rows or [])

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _Session:
    def __init__(self, schedule: object, agent: object) -> None:
        self._results = [_Result(rows=[schedule]), _Result(value=agent)]
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement: object) -> _Result:
        if not self._results:
            raise AssertionError("unexpected database query")
        return self._results.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


def _records(*, status: str = "idle") -> tuple[SimpleNamespace, SimpleNamespace]:
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        name="daily-summary",
        instruction="Prepare the daily summary",
        cron_expr="0 9 * * *",
        is_enabled=True,
        last_run_at=None,
        next_run_at=due_at,
        run_count=0,
    )
    agent = SimpleNamespace(
        id=schedule.agent_id,
        status=status,
        is_expired=False,
        expires_at=None,
    )
    return schedule, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_status", ["creating", "running", "idle"])
async def test_due_schedule_for_active_agent_is_enqueued_and_advanced(
    agent_status: str,
) -> None:
    schedule, agent = _records(status=agent_status)
    session = _Session(schedule, agent)
    handle = SimpleNamespace(run_id=uuid.uuid4())
    enqueue = AsyncMock(return_value=handle)

    with (
        patch("app.database.async_session", return_value=_SessionContext(session)),
        patch("app.services.audit_logger.write_audit_log", new=AsyncMock()),
        patch("app.services.heartbeat_runtime.enqueue_schedule_runtime", new=enqueue),
    ):
        await _tick()

    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["agent"] is agent
    assert enqueue.await_args.kwargs["schedule_id"] == schedule.id
    assert session.commits == 1
    assert session.rollbacks == 0
    assert schedule.run_count == 1
    assert schedule.last_run_at is not None
    assert schedule.next_run_at > schedule.last_run_at


@pytest.mark.asyncio
async def test_due_schedule_is_not_advanced_when_runtime_is_disabled() -> None:
    schedule, agent = _records(status="idle")
    original_next_run = schedule.next_run_at
    session = _Session(schedule, agent)

    with (
        patch("app.database.async_session", return_value=_SessionContext(session)),
        patch("app.services.audit_logger.write_audit_log", new=AsyncMock()),
        patch(
            "app.services.heartbeat_runtime.enqueue_schedule_runtime",
            new=AsyncMock(return_value=None),
        ) as enqueue,
    ):
        await _tick()

    enqueue.assert_awaited_once()
    assert session.commits == 0
    assert session.rollbacks == 1
    assert schedule.run_count == 0
    assert schedule.last_run_at is None
    assert schedule.next_run_at == original_next_run


@pytest.mark.asyncio
async def test_due_schedule_does_not_enqueue_for_stopped_agent() -> None:
    schedule, agent = _records(status="stopped")
    session = _Session(schedule, agent)
    enqueue = AsyncMock()

    with (
        patch("app.database.async_session", return_value=_SessionContext(session)),
        patch("app.services.audit_logger.write_audit_log", new=AsyncMock()),
        patch("app.services.heartbeat_runtime.enqueue_schedule_runtime", new=enqueue),
    ):
        await _tick()

    enqueue.assert_not_awaited()
