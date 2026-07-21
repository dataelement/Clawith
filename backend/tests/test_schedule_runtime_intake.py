"""Schedule API transaction boundary tests for Runtime intake."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException
import pytest

from app.api.schedules import trigger_schedule


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Session:
    def __init__(self, schedule: object, timeline: list[str]) -> None:
        self._schedule = schedule
        self.timeline = timeline

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._schedule)

    async def flush(self) -> None:
        self.timeline.append("business_fact_flushed")

    async def commit(self) -> None:
        raise AssertionError("schedule API must leave commit ownership to get_db")


@pytest.mark.asyncio
async def test_manual_schedule_registers_run_in_the_request_transaction() -> None:
    timeline: list[str] = []
    agent_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    agent = SimpleNamespace(id=agent_id, is_expired=False)
    schedule = SimpleNamespace(
        id=schedule_id,
        agent_id=agent_id,
        instruction="Prepare the weekly summary",
        last_run_at=None,
        run_count=0,
    )
    db = _Session(schedule, timeline)
    handle = SimpleNamespace(run_id=uuid.uuid4())

    async def enqueue(session, **kwargs):
        assert session is db
        assert kwargs["agent"] is agent
        assert kwargs["schedule_id"] == schedule_id
        timeline.append("runtime_registered")
        return handle

    with (
        patch(
            "app.api.schedules.check_agent_access",
            new=AsyncMock(return_value=(agent, "manage")),
        ),
        patch(
            "app.api.schedules.enqueue_schedule_runtime",
            new=AsyncMock(side_effect=enqueue),
        ),
    ):
        response = await trigger_schedule(
            agent_id=agent_id,
            schedule_id=schedule_id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response == {
        "status": "queued",
        "schedule_id": str(schedule_id),
        "run_id": str(handle.run_id),
    }
    assert timeline == ["runtime_registered", "business_fact_flushed"]
    assert schedule.run_count == 1
    assert schedule.last_run_at is not None


@pytest.mark.asyncio
async def test_manual_schedule_does_not_advance_when_runtime_is_disabled() -> None:
    agent_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, is_expired=False)
    schedule = SimpleNamespace(
        id=schedule_id,
        agent_id=agent_id,
        instruction="Prepare the weekly summary",
        last_run_at=None,
        run_count=0,
    )
    db = _Session(schedule, [])

    with (
        patch(
            "app.api.schedules.check_agent_access",
            new=AsyncMock(return_value=(agent, "manage")),
        ),
        patch(
            "app.api.schedules.enqueue_schedule_runtime",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as raised,
    ):
        await trigger_schedule(
            agent_id=agent_id,
            schedule_id=schedule_id,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=db,  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 503
    assert schedule.run_count == 0
    assert schedule.last_run_at is None
    assert db.timeline == []
