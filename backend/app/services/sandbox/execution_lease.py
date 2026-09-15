"""Redis-backed execution lease for one tenant/Agent/Session sandbox scope."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import uuid
from contextlib import suppress
from typing import Protocol

from loguru import logger

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_EXECUTOR_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


class SandboxLeaseRedis(Protocol):
    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> object: ...

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...


class SandboxLeaseScope(Protocol):
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID


class SandboxExecutionLease:
    def __init__(
        self,
        redis: SandboxLeaseRedis,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        self._redis = redis
        self.key = key
        self._value = value
        self.ttl_seconds = ttl_seconds
        self.ownership_lost = False
        self._stop = asyncio.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def correlation_id(self) -> str:
        return hashlib.sha256(self._value.encode()).hexdigest()[:12]

    async def _renew(self, seconds: int) -> bool:
        try:
            renewed = bool(
                await self._redis.eval(
                    _RENEW_SCRIPT,
                    1,
                    self.key,
                    self._value,
                    seconds * 1000,
                )
            )
        except Exception:  # noqa: BLE001 -- Redis failures make ownership unverifiable.
            logger.exception("[SandboxLease] Renewal unverifiable key={}", self.key)
            renewed = False
        if not renewed:
            self.ownership_lost = True
        return renewed

    async def start_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            return

        async def heartbeat() -> None:
            interval = max(1, self.ttl_seconds // 3)
            while True:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                    return
                except TimeoutError:
                    if not await self._renew(self.ttl_seconds):
                        return

        self._heartbeat_task = asyncio.create_task(heartbeat())

    async def ensure_publication_window(self, seconds: int) -> bool:
        self._stop.set()
        if self._heartbeat_task is not None:
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        return await self._renew(seconds)

    async def release(self) -> None:
        self._stop.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
        await asyncio.shield(
            self._redis.eval(_RELEASE_SCRIPT, 1, self.key, self._value)
        )


class SandboxExecutionLeaseStore:
    def __init__(self, redis: SandboxLeaseRedis) -> None:
        self._redis = redis

    @staticmethod
    def key(scope: SandboxLeaseScope) -> str:
        return (
            f"tenant:{scope.tenant_id}:sandbox-execution:"
            f"{scope.agent_id}:{scope.session_id}"
        )

    async def acquire(
        self,
        scope: SandboxLeaseScope,
        *,
        ttl_seconds: int = 60,
    ) -> SandboxExecutionLease | None:
        key = self.key(scope)
        value = f"v1|{_EXECUTOR_INSTANCE_ID}|{uuid.uuid4().hex}"
        acquired = await self._redis.set(
            key,
            value,
            nx=True,
            px=ttl_seconds * 1000,
        )
        if not acquired:
            return None
        return SandboxExecutionLease(self._redis, key, value, ttl_seconds)
