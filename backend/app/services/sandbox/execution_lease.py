"""Redis-backed execution lease for one tenant/Agent/Session sandbox scope."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import uuid
from contextlib import suppress

from loguru import logger

from app.core.events import get_redis
from app.services.sandbox.workspace_policy import SandboxExecutionScope

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


class SandboxExecutionLease:
    def __init__(self, key: str, value: str, ttl_seconds: int) -> None:
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
            redis = await get_redis()
            renewed = bool(await redis.eval(_RENEW_SCRIPT, 1, self.key, self._value, seconds * 1000))
        except Exception:
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
                except asyncio.TimeoutError:
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
        redis = await get_redis()
        await asyncio.shield(redis.eval(_RELEASE_SCRIPT, 1, self.key, self._value))


class SandboxExecutionLeaseStore:
    @staticmethod
    def key(scope: SandboxExecutionScope) -> str:
        return (
            f"tenant:{scope.tenant_id}:sandbox-execution:"
            f"{scope.agent_id}:{scope.session_id}"
        )

    async def acquire(
        self,
        scope: SandboxExecutionScope,
        *,
        ttl_seconds: int = 60,
    ) -> SandboxExecutionLease | None:
        key = self.key(scope)
        value = f"v1|{_EXECUTOR_INSTANCE_ID}|{uuid.uuid4().hex}"
        redis = await get_redis()
        acquired = await redis.set(key, value, nx=True, px=ttl_seconds * 1000)
        if not acquired:
            return None
        return SandboxExecutionLease(key, value, ttl_seconds)
