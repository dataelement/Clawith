"""PostgreSQL session-level advisory lock for one Agent Runtime thread."""

from collections.abc import Awaitable, Callable
import hashlib
from typing import TypeVar
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


T = TypeVar("T")

_ACQUIRE_SQL = sa.text("SELECT pg_try_advisory_lock(:lock_key)")
_RELEASE_SQL = sa.text("SELECT pg_advisory_unlock(:lock_key)")


class ThreadLockNotAcquired(RuntimeError):
    """Another worker currently owns the Run thread lock."""

    def __init__(self, thread_id: str | uuid.UUID, lock_key: int) -> None:
        super().__init__(f"Agent Runtime thread {thread_id} lock is already held")
        self.thread_id = str(thread_id)
        # Compatibility for existing metrics/tests while callers move to the
        # real Thread identity.
        self.run_id = thread_id
        self.lock_key = lock_key


class ThreadLockReleaseError(RuntimeError):
    """The dedicated connection did not own the lock at release time."""

    def __init__(self, thread_id: str | uuid.UUID, lock_key: int) -> None:
        super().__init__(f"Agent Runtime thread {thread_id} lock could not be released")
        self.thread_id = str(thread_id)
        self.run_id = thread_id
        self.lock_key = lock_key


def thread_lock_key(thread_id: str | uuid.UUID) -> int:
    """Derive one stable signed PostgreSQL bigint key from a Thread identity."""
    try:
        identity_bytes = (
            thread_id.bytes
            if isinstance(thread_id, uuid.UUID)
            else uuid.UUID(str(thread_id)).bytes
        )
    except ValueError:
        identity_bytes = str(thread_id).encode("utf-8")
    if not identity_bytes:
        raise ValueError("thread_id must not be blank")
    digest = hashlib.blake2b(
        identity_bytes,
        digest_size=8,
        person=b"clawith-run-v1",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def run_with_thread_lock(
    engine: AsyncEngine,
    thread_id: str | uuid.UUID,
    callback: Callable[[AsyncConnection], Awaitable[T]],
) -> T:
    """Run checkpoint/invoke/reconcile work on one locked connection.

    The advisory lock is session-scoped, so the same dedicated connection is
    passed to the callback and retained until the unlock query completes.
    Failure to acquire never invokes the callback.
    """
    lock_key = thread_lock_key(thread_id)
    async with engine.connect() as connection:
        acquired_result = await connection.execute(
            _ACQUIRE_SQL,
            {"lock_key": lock_key},
        )
        if not bool(acquired_result.scalar_one()):
            raise ThreadLockNotAcquired(thread_id, lock_key)

        try:
            return await callback(connection)
        finally:
            released_result = await connection.execute(
                _RELEASE_SQL,
                {"lock_key": lock_key},
            )
            if not bool(released_result.scalar_one()):
                raise ThreadLockReleaseError(thread_id, lock_key)
