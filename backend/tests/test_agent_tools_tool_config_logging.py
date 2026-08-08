from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services import agent_tools


class _Result:
    def scalar_one_or_none(self):
        return None


class _MissingToolConfigSession:
    async def execute(self, _statement):
        return _Result()


class _FailingToolConfigSession:
    async def execute(self, _statement):
        raise RuntimeError("database unavailable")


class _LoggerSpy:
    def __init__(self) -> None:
        self.debug_messages: list[str] = []
        self.error_messages: list[str] = []

    def debug(self, message: str) -> None:
        self.debug_messages.append(message)

    def error(self, message: str) -> None:
        self.error_messages.append(message)


@pytest.mark.asyncio
async def test_missing_optional_tool_config_is_debug_not_error(monkeypatch):
    @asynccontextmanager
    async def session_factory():
        yield _MissingToolConfigSession()

    logger = _LoggerSpy()
    monkeypatch.setattr(agent_tools, "async_session", session_factory)
    monkeypatch.setattr(agent_tools, "logger", logger)
    agent_tools._tool_config_cache.clear()

    assert await agent_tools._get_tool_config(None, "optional_tool") is None
    assert logger.error_messages == []
    assert any("No DB config found" in message for message in logger.debug_messages)


@pytest.mark.asyncio
async def test_tool_config_database_errors_are_not_hidden(monkeypatch):
    @asynccontextmanager
    async def session_factory():
        yield _FailingToolConfigSession()

    monkeypatch.setattr(agent_tools, "async_session", session_factory)
    agent_tools._tool_config_cache.clear()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await agent_tools._get_tool_config(None, "optional_tool")
