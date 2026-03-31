"""DingTalk access_token global cache manager.

Caches tokens per app_key with auto-refresh before expiry.
All DingTalk token acquisition should go through this manager.
Tokens are stored in Redis (preferred) with in-memory fallback.
"""

import time
import asyncio
from typing import Dict, Optional
from loguru import logger
import httpx


class DingTalkTokenManager:
    """Global DingTalk access_token cache backed by Redis + memory fallback.

    - Cache by app_key
    - Token valid for 7200s, refresh 300s early (TTL=6900)
    - Concurrency-safe with asyncio.Lock
    """

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, app_key: str) -> asyncio.Lock:
        if app_key not in self._locks:
            self._locks[app_key] = asyncio.Lock()
        return self._locks[app_key]

    def _cache_key(self, app_key: str) -> str:
        return f"clawith:token:dingtalk_corp:{app_key}"

    async def get_token(self, app_key: str, app_secret: str) -> Optional[str]:
        """Get access_token, return cached if valid, refresh if expired."""
        from app.core.token_cache import get_cached_token, set_cached_token

        key = self._cache_key(app_key)

        # Fast path: check cache without lock
        cached = await get_cached_token(key)
        if cached:
            return cached

        async with self._get_lock(app_key):
            # Double-check after acquiring lock
            cached = await get_cached_token(key)
            if cached:
                return cached

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                        json={"appKey": app_key, "appSecret": app_secret},
                    )
                    data = resp.json()
                    token = data.get("accessToken")
                    expires_in = data.get("expireIn", 7200)

                    if token:
                        # TTL = expires_in - 300s (refresh 5 min early)
                        ttl = max(expires_in - 300, 60)
                        await set_cached_token(key, token, ttl)
                        logger.debug(f"[DingTalk Token] Refreshed for {app_key[:8]}..., expires in {expires_in}s, TTL={ttl}s")
                        return token

                    logger.error(f"[DingTalk Token] Failed to get token: {data}")
                    return None
            except Exception as e:
                logger.error(f"[DingTalk Token] Error getting token: {e}")
                return None


# Global singleton
dingtalk_token_manager = DingTalkTokenManager()
