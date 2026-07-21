"""Pure connection-lifecycle tests for the Runtime thread advisory lock."""

import uuid

import pytest

from app.services.agent_runtime.thread_lock import (
    ThreadLockNotAcquired,
    ThreadLockReleaseError,
    run_with_thread_lock,
    thread_lock_key,
)


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _Connection:
    def __init__(self, *, acquired: bool = True, released: bool = True) -> None:
        self.acquired = acquired
        self.released = released
        self.events: list[tuple[str, dict[str, int] | None]] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        self.events.append(("connection_enter", None))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True
        self.events.append(("connection_exit", None))

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.events.append((sql, parameters))
        if "pg_try_advisory_lock" in sql:
            return _ScalarResult(self.acquired)
        if "pg_advisory_unlock" in sql:
            return _ScalarResult(self.released)
        return _ScalarResult(True)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.connect_calls = 0

    def connect(self) -> _Connection:
        self.connect_calls += 1
        return self.connection


def test_thread_lock_key_is_stable_signed_bigint() -> None:
    run_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    assert thread_lock_key(run_id) == 1175056106503917823
    assert thread_lock_key(run_id) == thread_lock_key(run_id)
    assert -(2**63) <= thread_lock_key(run_id) < 2**63
    assert thread_lock_key(run_id) != thread_lock_key(uuid.uuid4())


@pytest.mark.asyncio
async def test_callback_uses_same_dedicated_connection_until_unlock() -> None:
    run_id = uuid.uuid4()
    connection = _Connection()
    engine = _Engine(connection)

    async def callback(callback_connection):
        assert callback_connection is connection
        connection.events.append(("checkpoint_invoke_reconcile", None))
        return "done"

    result = await run_with_thread_lock(engine, run_id, callback)  # type: ignore[arg-type]

    lock_key = thread_lock_key(run_id)
    assert result == "done"
    assert engine.connect_calls == 1
    assert connection.entered is True
    assert connection.exited is True
    assert connection.events == [
        ("connection_enter", None),
        ("SELECT pg_try_advisory_lock(:lock_key)", {"lock_key": lock_key}),
        ("checkpoint_invoke_reconcile", None),
        ("SELECT pg_advisory_unlock(:lock_key)", {"lock_key": lock_key}),
        ("connection_exit", None),
    ]


@pytest.mark.asyncio
async def test_not_acquired_is_typed_and_never_invokes_callback() -> None:
    run_id = uuid.uuid4()
    connection = _Connection(acquired=False)
    engine = _Engine(connection)
    invoked = False

    async def callback(_connection):
        nonlocal invoked
        invoked = True

    with pytest.raises(ThreadLockNotAcquired) as exc_info:
        await run_with_thread_lock(engine, run_id, callback)  # type: ignore[arg-type]

    assert invoked is False
    assert exc_info.value.run_id == run_id
    assert exc_info.value.lock_key == thread_lock_key(run_id)
    assert connection.exited is True
    assert all("pg_advisory_unlock" not in event[0] for event in connection.events)


@pytest.mark.asyncio
async def test_callback_error_still_releases_before_connection_exit() -> None:
    run_id = uuid.uuid4()
    connection = _Connection()
    engine = _Engine(connection)

    async def callback(_connection):
        connection.events.append(("callback_error", None))
        raise LookupError("invoke failed")

    with pytest.raises(LookupError, match="invoke failed"):
        await run_with_thread_lock(engine, run_id, callback)  # type: ignore[arg-type]

    event_names = [event[0] for event in connection.events]
    assert event_names == [
        "connection_enter",
        "SELECT pg_try_advisory_lock(:lock_key)",
        "callback_error",
        "SELECT pg_advisory_unlock(:lock_key)",
        "connection_exit",
    ]


@pytest.mark.asyncio
async def test_failed_unlock_returns_typed_release_error() -> None:
    run_id = uuid.uuid4()
    connection = _Connection(released=False)
    engine = _Engine(connection)

    async def callback(_connection):
        return "done"

    with pytest.raises(ThreadLockReleaseError) as exc_info:
        await run_with_thread_lock(engine, run_id, callback)  # type: ignore[arg-type]

    assert exc_info.value.run_id == run_id
    assert exc_info.value.lock_key == thread_lock_key(run_id)
    assert connection.exited is True
