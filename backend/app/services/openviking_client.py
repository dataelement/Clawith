"""OpenViking HTTP client for semantic memory retrieval.

Provides optional integration with a local OpenViking server to replace
full memory.md injection with relevance-ranked snippets.

Requires:
    openviking-server --workers 4 running at OPENVIKING_URL (default: http://127.0.0.1:1933)

Falls back gracefully if OpenViking is unavailable.
"""

import os
import time
from pathlib import Path

import httpx
from loguru import logger

OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933").rstrip("/")
OPENVIKING_TIMEOUT = float(os.environ.get("OPENVIKING_TIMEOUT", "3.0"))
OPENVIKING_LIMIT = int(os.environ.get("OPENVIKING_LIMIT", "5"))
OPENVIKING_SCORE_THRESHOLD = float(os.environ.get("OPENVIKING_SCORE_THRESHOLD", "0.35"))

_CLAWITH_SCOPE = "viking://clawith"

# ── 持久化连接池（进程级单例）───────────────────────────────
# limits: 最多 20 个并发连接，与 Clawith Agent 数量匹配
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=OPENVIKING_URL,
            timeout=OPENVIKING_TIMEOUT,
            limits=httpx.Limits(
                max_connections=30,        # 最多 30 个连接（20 Agent + 余量）
                max_keepalive_connections=20,  # 保持 20 个长连接
                keepalive_expiry=30,       # 空闲 30s 后关闭
            ),
        )
    return _client


# ── 可用性缓存（避免每次请求都做健康检查）─────────────────
_available: bool | None = None
_available_checked_at: float = 0.0
_AVAILABILITY_TTL = 30.0  # 30秒内复用上次结果


async def is_available() -> bool:
    """带缓存的健康检查，30 秒内只真正检查一次。"""
    global _available, _available_checked_at
    now = time.monotonic()
    if _available is not None and now - _available_checked_at < _AVAILABILITY_TTL:
        return _available
    try:
        resp = await _get_client().get("/api/v1/system/health", timeout=1.0)
        _available = resp.status_code < 500
    except Exception:
        _available = False
    _available_checked_at = now
    return _available


def _invalidate_availability_cache() -> None:
    """连接出错时重置缓存，下次强制重新检查。"""
    global _available, _available_checked_at
    _available = None
    _available_checked_at = 0.0


def _agent_scope(agent_id: str) -> str:
    return f"{_CLAWITH_SCOPE}/{agent_id}"


async def search_memory(query: str, agent_id: str, limit: int = OPENVIKING_LIMIT) -> list[str]:
    """Query OpenViking for memories relevant to *query* scoped to *agent_id*.

    Uses persistent connection pool. Returns [] on any failure.
    """
    try:
        resp = await _get_client().post(
            "/api/v1/search/find",
            json={
                "query": query,
                "target_uri": _agent_scope(agent_id),
                "limit": limit,
                "score_threshold": OPENVIKING_SCORE_THRESHOLD,
            },
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        memories = data.get("result", {}).get("memories", [])
        snippets = []
        for m in memories:
            content = m.get("content") or m.get("abstract") or ""
            if content:
                snippets.append(content.strip())
        return snippets

    except httpx.ConnectError:
        _invalidate_availability_cache()
        logger.debug("[OpenViking] connection lost, availability cache cleared")
        return []
    except Exception as e:
        logger.debug(f"[OpenViking] search_memory failed: {e}")
        return []


async def index_memory_file(agent_id: str, file_path: Path) -> bool:
    """Reindex a memory file under the agent's OpenViking scope (non-blocking).

    Called after agent writes to memory.md.
    """
    if not file_path.exists():
        return False
    try:
        resp = await _get_client().post(
            "/api/v1/resources",
            json={
                "path": str(file_path),
                "to": _agent_scope(agent_id),
                "wait": False,
            },
            timeout=OPENVIKING_TIMEOUT * 2,
        )
        ok = resp.status_code < 300
        if not ok:
            logger.debug(f"[OpenViking] index_memory_file failed: {resp.status_code}")
        return ok
    except httpx.ConnectError:
        _invalidate_availability_cache()
        return False
    except Exception as e:
        logger.debug(f"[OpenViking] index_memory_file error: {e}")
        return False
