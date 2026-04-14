"""进程内 TTL 缓存 helper, 服务于 dingtalk_service 的 token/user_detail。

设计要点:
- 单节点内存缓存: 多副本各自缓存, 不致命(token 每副本独立拉取;
  user_detail 不变, 重复拉取只是多一次请求, 不会引发业务错误)。
- single-flight: 同 key 并发调用时合并为一次 factory 执行, 避免 thundering herd。
- 失败结果不进入缓存: 由调用方在 factory 返回无效值后主动 invalidate。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, default_ttl: float = 60.0) -> None:
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: float | None = None,
    ) -> Any:
        now = time.monotonic()
        hit = self._store.get(key)
        if hit and hit[0] > now:
            return hit[1]

        lock = self._lock(key)
        async with lock:
            hit = self._store.get(key)
            now = time.monotonic()
            if hit and hit[0] > now:
                return hit[1]
            value = await factory()
            expire_at = now + (ttl if ttl is not None else self._default_ttl)
            self._store[key] = (expire_at, value)
            return value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)
