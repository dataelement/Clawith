"""Operation-owned PostgreSQL advisory locks on a dedicated non-business pool."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@dataclass
class _Lease:
    connection: AsyncConnection
    task: asyncio.Task[object]
    active: bool = True


class PostgresResourceLocks:
    def __init__(self, engine: AsyncEngine, *, timeout_seconds: float):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._engine = engine
        self._timeout_seconds = timeout_seconds
        self._lease: ContextVar[_Lease | None] = ContextVar(f"storage-lock-lease-{id(self)}", default=None)

    @asynccontextmanager
    async def __call__(self, key: str) -> AsyncIterator[None]:
        lock_id = int.from_bytes(hashlib.sha256(("clawith-storage:" + key).encode()).digest()[:8], signed=True)
        lease = self._lease.get()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Storage resource locks require an asyncio Task")
        if lease is not None and lease.active and lease.task is not task:
            raise RuntimeError("An active storage lock lease cannot be inherited by another Task")
        outermost = lease is None or not lease.active
        token = None
        if outermost:
            try:
                connection = await asyncio.wait_for(self._engine.connect(), self._timeout_seconds)
            except SQLAlchemyError as exc:
                raise OSError("Storage resource lock connection failed") from exc
            lease = _Lease(connection, task)
            token = self._lease.set(lease)
        else:
            assert lease is not None
            connection = lease.connection
            if connection.invalidated:
                raise OSError("Storage resource lock session was lost")
        assert lease is not None
        try:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    if outermost:
                        await connection.execution_options(isolation_level="AUTOCOMMIT")
                    while not await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}):
                        await asyncio.sleep(0.01)
            except SQLAlchemyError as exc:
                raise OSError("Storage resource lock acquisition failed") from exc
            yield
        finally:
            if outermost:
                lease.active = False
                if token is not None:
                    self._lease.reset(token)
            cleanup = asyncio.create_task(_release(connection, lock_id, close=outermost))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
                raise


async def _release(connection: AsyncConnection, lock_id: int, *, close: bool) -> None:
    try:
        try:
            async with asyncio.timeout(5):
                # Also release an acquisition whose result was lost to cancellation.
                if not connection.invalidated:
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
        except BaseException as exc:
            # A session-level lock must never escape back into the pool.
            await connection.invalidate()
            if isinstance(exc, SQLAlchemyError):
                raise OSError("Storage resource lock release failed") from exc
            raise
    finally:
        if close:
            await connection.close()
