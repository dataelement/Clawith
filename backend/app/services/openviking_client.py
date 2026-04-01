"""OpenViking HTTP client for semantic memory retrieval.

Provides optional integration with a local OpenViking server to replace
full memory.md injection with relevance-ranked snippets.

Requires:
    openviking-server running at OPENVIKING_URL (default: http://127.0.0.1:1933)

Falls back gracefully if OpenViking is unavailable.
"""

import os
from pathlib import Path

import httpx
from loguru import logger

OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933").rstrip("/")
OPENVIKING_TIMEOUT = float(os.environ.get("OPENVIKING_TIMEOUT", "3.0"))  # seconds
OPENVIKING_LIMIT = int(os.environ.get("OPENVIKING_LIMIT", "5"))
OPENVIKING_SCORE_THRESHOLD = float(os.environ.get("OPENVIKING_SCORE_THRESHOLD", "0.35"))

# Agent-scoped URI prefix in OpenViking
# Each Clawith agent maps to a sub-path under viking://clawith/
_CLAWITH_SCOPE = "viking://clawith"


def _agent_scope(agent_id: str) -> str:
    return f"{_CLAWITH_SCOPE}/{agent_id}"


async def is_available() -> bool:
    """Quick health check — returns False if OpenViking is not reachable."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{OPENVIKING_URL}/api/v1/system/health")
            return resp.status_code < 500
    except Exception:
        return False


async def search_memory(query: str, agent_id: str, limit: int = OPENVIKING_LIMIT) -> list[str]:
    """Query OpenViking for memories relevant to *query* scoped to *agent_id*.

    Returns a list of text snippets ordered by relevance.
    Returns an empty list if OpenViking is unavailable or returns no results.
    """
    try:
        async with httpx.AsyncClient(timeout=OPENVIKING_TIMEOUT) as client:
            resp = await client.post(
                f"{OPENVIKING_URL}/api/v1/search/find",
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
            result = data.get("result", {})
            memories = result.get("memories", [])
            snippets = []
            for m in memories:
                content = m.get("content") or m.get("abstract") or ""
                if content:
                    snippets.append(content.strip())
            return snippets

    except Exception as e:
        logger.debug(f"[OpenViking] search_memory failed: {e}")
        return []


async def index_memory_file(agent_id: str, file_path: Path) -> bool:
    """Register/reindex a memory file under the agent's OpenViking scope.

    Called after agent writes to memory.md so OpenViking keeps its index fresh.
    Returns True on success, False on failure (non-fatal).
    """
    if not file_path.exists():
        return False
    try:
        async with httpx.AsyncClient(timeout=OPENVIKING_TIMEOUT * 2) as client:
            resp = await client.post(
                f"{OPENVIKING_URL}/api/v1/resources",
                json={
                    "path": str(file_path),
                    "to": _agent_scope(agent_id),
                    "wait": False,  # async indexing, don't block the write
                },
            )
            ok = resp.status_code < 300
            if not ok:
                logger.debug(f"[OpenViking] index_memory_file failed: {resp.status_code} {resp.text[:200]}")
            return ok
    except Exception as e:
        logger.debug(f"[OpenViking] index_memory_file error: {e}")
        return False
