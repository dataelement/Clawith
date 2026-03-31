"""
Unified Redis-backed token cache with in-memory fallback.

Key naming convention: clawith:token:{type}:{identifier}
Examples:
  clawith:token:dingtalk_corp:{app_key}
  clawith:token:feishu_tenant:{app_id}
  clawith:token:wecom:{corp_id}:{secret_hash}
  clawith:token:teams:{agent_id}
"""
import time
from typing import Optional

# In-memory fallback store: {key: (value, expire_at)}
_memory_cache: dict[str, tuple[str, float]] = {}


async def get_cached_token(key: str) -> Optional[str]:
    """Get token from Redis (preferred) or memory fallback."""
    # Try Redis first
    try:
        from app.core.events import get_redis
        redis = await get_redis()
        if redis:
            val = await redis.get(key)
            if val:
                return val.decode() if isinstance(val, bytes) else val
    except Exception:
        pass

    # Fallback to memory
    if key in _memory_cache:
        val, expire_at = _memory_cache[key]
        if time.time() < expire_at:
            return val
        del _memory_cache[key]
    return None


async def set_cached_token(key: str, value: str, ttl_seconds: int) -> None:
    """Set token in Redis (preferred) and memory fallback."""
    # Try Redis first
    try:
        from app.core.events import get_redis
        redis = await get_redis()
        if redis:
            await redis.setex(key, ttl_seconds, value)
    except Exception:
        pass

    # Always set in memory as fallback
    _memory_cache[key] = (value, time.time() + ttl_seconds)


async def delete_cached_token(key: str) -> None:
    """Delete token from both Redis and memory."""
    try:
        from app.core.events import get_redis
        redis = await get_redis()
        if redis:
            await redis.delete(key)
    except Exception:
        pass
    _memory_cache.pop(key, None)
