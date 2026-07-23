"""Regression coverage for AgentSchedule scheduler startup wiring."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.main as main
from app.services import audit_logger, scheduler, trigger_daemon
from app.services.agent_runtime import worker_service


class _Task:
    def __init__(self, coro, name: str) -> None:
        self._name = name
        coro.close()

    def add_done_callback(self, _callback) -> None:
        return None

    def get_name(self) -> str:
        return self._name

    def exception(self):
        return None


async def _collect_background_task_names(monkeypatch, *, process_role: str) -> list[str]:
    created: list[str] = []

    def create_task(coro, *, name: str) -> _Task:
        created.append(name)
        return _Task(coro, name)

    @asynccontextmanager
    async def runtime_context(**_kwargs):
        yield

    monkeypatch.setattr(main.settings, "PROCESS_ROLE", process_role)
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "intercept_standard_logging", lambda: None)
    monkeypatch.setattr(main, "_log_bwrap_startup_status", lambda: None)
    monkeypatch.setattr(asyncio, "create_task", create_task)
    monkeypatch.setattr(main, "_start_ss_local", AsyncMock())
    monkeypatch.setattr(main, "close_redis", AsyncMock())
    monkeypatch.setattr(main.realtime_router, "start", AsyncMock())
    monkeypatch.setattr(main.realtime_router, "stop", AsyncMock())
    monkeypatch.setattr(audit_logger, "write_audit_log", AsyncMock())
    monkeypatch.setattr(trigger_daemon, "start_trigger_daemon", AsyncMock())
    monkeypatch.setattr(scheduler, "start_scheduler", AsyncMock())
    monkeypatch.setattr(worker_service, "running_runtime_worker_context", runtime_context)

    async with main.lifespan(main.app):
        pass

    return created


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("process_role", "expected"),
    [
        ("worker", True),
        ("api", False),
    ],
)
async def test_agent_schedule_scheduler_follows_worker_role(
    monkeypatch,
    process_role: str,
    expected: bool,
) -> None:
    names = await _collect_background_task_names(monkeypatch, process_role=process_role)

    assert ("trigger_daemon" in names) is expected
    assert ("agent_schedule_scheduler" in names) is expected
