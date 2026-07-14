"""Transaction-bound after-commit callback regression tests."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app import database
from app.database import (
    add_after_commit_callback,
    add_after_rollback_callback,
    transaction,
)


class _RecordingSession:
    def __init__(self, events: list[str], *, commit_error: Exception | None = None) -> None:
        self.events = events
        self.info = {}
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_error = commit_error

    async def commit(self) -> None:
        self.commit_count += 1
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.events.append("rollback")


@pytest.mark.asyncio
async def test_after_commit_callback_runs_only_after_commit() -> None:
    events = []
    session = _RecordingSession(events)

    async def callback() -> None:
        events.append("callback")

    async def rollback_callback() -> None:
        events.append("rollback_callback")

    async with transaction(session):  # type: ignore[arg-type]
        add_after_commit_callback(session, callback)  # type: ignore[arg-type]
        add_after_rollback_callback(session, rollback_callback)  # type: ignore[arg-type]
        events.append("body")
        assert events == ["body"]

    assert events == ["body", "commit", "callback"]
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert session.info == {}


@pytest.mark.asyncio
async def test_rollback_discards_staged_after_commit_callbacks() -> None:
    events = []
    session = _RecordingSession(events)

    async def callback() -> None:
        events.append("callback")

    async def rollback_callback() -> None:
        events.append("rollback_callback")

    with pytest.raises(RuntimeError, match="abort transaction"):
        async with transaction(session):  # type: ignore[arg-type]
            add_after_commit_callback(session, callback)  # type: ignore[arg-type]
            add_after_rollback_callback(session, rollback_callback)  # type: ignore[arg-type]
            raise RuntimeError("abort transaction")

    assert events == ["rollback", "rollback_callback"]
    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert session.info == {}


@pytest.mark.asyncio
async def test_callback_failure_does_not_rollback_committed_transaction() -> None:
    events = []
    session = _RecordingSession(events)

    async def failing_callback() -> None:
        events.append("callback")
        raise RuntimeError("realtime transport unavailable")

    async with transaction(session):  # type: ignore[arg-type]
        add_after_commit_callback(session, failing_callback)  # type: ignore[arg-type]

    assert events == ["commit", "callback"]
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert session.info == {}


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_runs_compensation() -> None:
    events = []
    original_error = RuntimeError("database commit failed")
    session = _RecordingSession(events, commit_error=original_error)

    async def rollback_callback() -> None:
        events.append("rollback_callback")

    with pytest.raises(RuntimeError, match="database commit failed") as exc_info:
        async with transaction(session):  # type: ignore[arg-type]
            add_after_rollback_callback(session, rollback_callback)  # type: ignore[arg-type]

    assert exc_info.value is original_error
    assert events == ["commit", "rollback", "rollback_callback"]
    assert session.commit_count == 1
    assert session.rollback_count == 1
    assert session.info == {}


@pytest.mark.asyncio
async def test_get_db_commit_failure_runs_rollback_compensation(monkeypatch) -> None:
    events = []
    original_error = RuntimeError("database commit failed")
    session = _RecordingSession(events, commit_error=original_error)

    @asynccontextmanager
    async def session_factory():
        yield session

    monkeypatch.setattr(database, "async_session", session_factory)
    dependency = database.get_db()
    yielded_session = await anext(dependency)

    async def rollback_callback() -> None:
        events.append("rollback_callback")

    add_after_rollback_callback(yielded_session, rollback_callback)
    with pytest.raises(RuntimeError, match="database commit failed") as exc_info:
        await anext(dependency)

    assert exc_info.value is original_error
    assert events == ["commit", "rollback", "rollback_callback"]
    assert session.info == {}


@pytest.mark.asyncio
async def test_rollback_callbacks_run_lifo_and_failure_does_not_mask_error() -> None:
    events = []
    original_error = RuntimeError("abort transaction")
    session = _RecordingSession(events)

    async def failing_callback() -> None:
        events.append("failing_callback")
        raise RuntimeError("storage unavailable")

    async def following_callback() -> None:
        events.append("following_callback")

    with pytest.raises(RuntimeError, match="abort transaction") as exc_info:
        async with transaction(session):  # type: ignore[arg-type]
            add_after_rollback_callback(session, failing_callback)  # type: ignore[arg-type]
            add_after_rollback_callback(session, following_callback)  # type: ignore[arg-type]
            raise original_error

    assert exc_info.value is original_error
    assert events == ["rollback", "following_callback", "failing_callback"]
    assert session.info == {}
