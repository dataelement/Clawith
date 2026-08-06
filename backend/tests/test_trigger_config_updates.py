"""Validation at the existing Trigger update boundaries."""

from __future__ import annotations

from contextlib import asynccontextmanager
import uuid

from fastapi import HTTPException
import pytest

from app.api import triggers as triggers_api
from app.models.trigger import AgentTrigger
from app.services import agent_tools, audit_logger


class _ScalarResult:
    def __init__(self, value: AgentTrigger) -> None:
        self._value = value

    def scalar_one_or_none(self) -> AgentTrigger:
        return self._value


class _TriggerSession:
    def __init__(self, trigger: AgentTrigger) -> None:
        self._trigger = trigger
        self.commit_count = 0

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self._trigger)

    async def commit(self) -> None:
        self.commit_count += 1


def _cron_trigger() -> AgentTrigger:
    return AgentTrigger(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        name="daily-check",
        type="cron",
        config={"expr": "0 9 * * *"},
        reason="Daily check",
        is_enabled=True,
        fire_count=0,
        cooldown_seconds=60,
    )


@pytest.mark.asyncio
async def test_rest_update_rejects_invalid_cron_before_commit(monkeypatch) -> None:
    trigger = _cron_trigger()
    session = _TriggerSession(trigger)

    @asynccontextmanager
    async def fake_session():
        yield session

    monkeypatch.setattr(triggers_api.query_dao, "session", fake_session)

    with pytest.raises(HTTPException) as error:
        await triggers_api.update_trigger(
            trigger.agent_id,
            trigger.id,
            triggers_api.TriggerUpdate(config={"expr": "not-a-cron"}),
            user=object(),
        )

    assert error.value.status_code == 400
    assert trigger.config == {"expr": "0 9 * * *"}
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_rest_update_accepts_valid_cron(monkeypatch) -> None:
    trigger = _cron_trigger()
    session = _TriggerSession(trigger)

    @asynccontextmanager
    async def fake_session():
        yield session

    monkeypatch.setattr(triggers_api.query_dao, "session", fake_session)

    result = await triggers_api.update_trigger(
        trigger.agent_id,
        trigger.id,
        triggers_api.TriggerUpdate(config={"expr": "30 9 * * 1-5"}),
        user=object(),
    )

    assert result == {"ok": True}
    assert trigger.config == {"expr": "30 9 * * 1-5"}
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_agent_tool_update_rejects_invalid_cron_before_commit(
    monkeypatch,
) -> None:
    trigger = _cron_trigger()
    session = _TriggerSession(trigger)

    @asynccontextmanager
    async def fake_session():
        yield session

    monkeypatch.setattr(agent_tools, "async_session", fake_session)

    outcome = await agent_tools._handle_update_trigger_outcome(
        trigger.agent_id,
        {"name": trigger.name, "config": {"expr": "not-a-cron"}},
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "invalid_tool_arguments"
    assert trigger.config == {"expr": "0 9 * * *"}
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_agent_tool_partial_update_keeps_valid_existing_cron(
    monkeypatch,
) -> None:
    trigger = _cron_trigger()
    session = _TriggerSession(trigger)

    @asynccontextmanager
    async def fake_session():
        yield session

    async def fake_audit_log(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(agent_tools, "async_session", fake_session)
    monkeypatch.setattr(audit_logger, "write_audit_log", fake_audit_log)

    outcome = await agent_tools._handle_update_trigger_outcome(
        trigger.agent_id,
        {
            "name": trigger.name,
            "config": {"timezone": "America/New_York"},
        },
    )

    assert outcome.status == "succeeded"
    assert trigger.config == {
        "expr": "0 9 * * *",
        "timezone": "America/New_York",
    }
    assert session.commit_count == 1
