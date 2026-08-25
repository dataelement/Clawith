"""Redis-backed short-lived locks for workspace mutations."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from app.core.events import get_redis

LOCK_PREFIX = "workspace-lock"
DEFAULT_LOCK_TTL_SECONDS = 60

_RELEASE_IF_OWNER_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _normalize_workspace_path(path: str) -> str:
    clean = (path or "").replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for part in clean.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _lock_key(agent_id: uuid.UUID, path: str, tenant_id: uuid.UUID | str | None = None) -> str:
    normalized = _normalize_workspace_path(path) or "."
    if tenant_id is not None:
        return f"tenant:{tenant_id}:workspace-lock:{agent_id}:{normalized}"
    return f"{LOCK_PREFIX}:{agent_id}:{normalized}"


async def acquire_workspace_lock(
    agent_id: uuid.UUID,
    path: str,
    *,
    owner_token: str,
    tenant_id: uuid.UUID | str | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> bool:
    redis = await get_redis()
    return bool(await redis.set(_lock_key(agent_id, path, tenant_id), owner_token, ex=ttl_seconds, nx=True))


async def release_workspace_lock(
    agent_id: uuid.UUID,
    path: str,
    *,
    owner_token: str,
    tenant_id: uuid.UUID | str | None = None,
) -> None:
    redis = await get_redis()
    await redis.eval(_RELEASE_IF_OWNER_SCRIPT, 1, _lock_key(agent_id, path, tenant_id), owner_token)


@asynccontextmanager
async def workspace_locks(
    agent_id: uuid.UUID,
    paths: list[str],
    *,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    tenant_id: uuid.UUID | str | None = None,
):
    normalized = sorted({_normalize_workspace_path(path) or "." for path in paths if path is not None})
    owner_token = uuid.uuid4().hex
    acquired: list[str] = []
    try:
        for path in normalized:
            ok = await acquire_workspace_lock(
                agent_id,
                path,
                owner_token=owner_token,
                tenant_id=tenant_id,
                ttl_seconds=ttl_seconds,
            )
            if not ok:
                raise RuntimeError(f"Workspace lock busy: {path}")
            acquired.append(path)
        yield
    finally:
        for path in reversed(acquired):
            await release_workspace_lock(agent_id, path, owner_token=owner_token, tenant_id=tenant_id)
