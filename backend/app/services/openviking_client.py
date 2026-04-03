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

OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933")
OPENVIKING_API_KEY = os.environ.get("OPENVIKING_API_KEY", "")

# Cache availability result for 30 seconds to avoid repeated checks
_last_available_check = 0.0
_cached_available: bool | None = None

def _get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENVIKING_API_KEY:
        headers["Authorization"] = f"Bearer {OPENVIKING_API_KEY}"
    return headers

async def is_available() -> bool:
    """Check if OpenViking server is reachable and healthy. Cache result for 30 seconds."""
    global _last_available_check, _cached_available

    now = time.time()
    if _cached_available is not None and (now - _last_available_check) < 30:
        return _cached_available

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{OPENVIKING_URL}/health",
                headers=_get_headers(),
                timeout=5.0
            )
            _cached_available = response.status_code == 200
            if _cached_available:
                logger.info("[OpenViking] Health check passed")
            else:
                logger.warning(f"[OpenViking] Health check failed: {response.status_code}")
    except Exception as e:
        _cached_available = False
        logger.warning(f"[OpenViking] Health check failed: {e}")

    _last_available_check = now
    return _cached_available

async def search_memory(
    query: str,
    agent_id: str,
    top_k: int = 5,
    grep_filter: str | None = None,
) -> list[str]:
    """Search agent's personal memory for semantic similarity to query."""
    if not await is_available():
        return []
    try:
        body: dict = {
            "query": query,
            "target_uri": _agent_scope(agent_id),
            "limit": top_k,
            "score_threshold": OPENVIKING_SCORE_THRESHOLD,
        }
        if grep_filter:
            body["grep_filter"] = grep_filter
        resp = await _get_client().post("/api/v1/search/find", json=body, timeout=OPENVIKING_TIMEOUT)
        if resp.status_code != 200:
            return []
        memories = resp.json().get("result", {}).get("memories", [])
        return [m.get("content") or m.get("abstract") or "" for m in memories if m.get("content") or m.get("abstract")]
    except httpx.ConnectError:
        _invalidate_availability_cache()
        return []
    except Exception as e:
        logger.debug(f"[OpenViking] search_memory failed: {e}")
        return []


async def index_memory_file(agent_id: str, file_path: Path) -> bool:
    """Reindex a memory file under the agent's OpenViking scope (non-blocking)."""
    file_path = Path(file_path)
    if not file_path.exists():
        return False
    if not await is_available():
        return False
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        resp = await _get_client().post(
            "/api/v1/skills",
            json={"data": content, "wait": False},
            headers={"X-OpenViking-Agent": agent_id},
            timeout=OPENVIKING_TIMEOUT * 2,
        )
        return resp.status_code < 300
    except httpx.ConnectError:
        _invalidate_availability_cache()
        return False
    except Exception as e:
        logger.debug(f"[OpenViking] index_memory_file error: {e}")
        return False


async def search_enterprise(
    query: str,
    top_k: int = 5,
    grep_filter: str | None = None,
) -> list[str]:
    """Search enterprise shared information from enterprise_info/ scope."""
    if not await is_available():
        return []
    try:
        body: dict = {
            "query": query,
            "target_uri": _ENTERPRISE_SCOPE,
            "limit": top_k,
            "score_threshold": OPENVIKING_SCORE_THRESHOLD,
        }
        if grep_filter:
            body["grep_filter"] = grep_filter
        resp = await _get_client().post("/api/v1/search/find", json=body, timeout=OPENVIKING_TIMEOUT)
        if resp.status_code != 200:
            return []
        memories = resp.json().get("result", {}).get("memories", [])
        return [m.get("content") or m.get("abstract") or "" for m in memories if m.get("content") or m.get("abstract")]
    except httpx.ConnectError:
        _invalidate_availability_cache()
        return []
    except Exception as e:
        logger.debug(f"[OpenViking] search_enterprise failed: {e}")
        return []

def _find_enterprise_dir(root_path: Path) -> Path | None:
    """Find enterprise_info directory, trying multiple locations."""
    # Try given root path
    candidate = root_path / "enterprise_info"
    if candidate.exists():
        return candidate
    # Try current working directory (where agent runs)
    candidate = Path.cwd() / "enterprise_info"
    if candidate.exists():
        return candidate
    return None

def _find_skills_dir(root_path: Path) -> Path | None:
    """Find skills directory, trying multiple locations."""
    # Try given root path
    candidate = root_path / "skills"
    if candidate.exists():
        return candidate
    # Try current working directory (where agent runs)
    candidate = Path.cwd() / "skills"
    if candidate.exists():
        return candidate
    return None

async def index_enterprise_info(root_path: Path) -> bool:
    """
    Index all markdown files in enterprise_info/ directory into enterprise scope.
    Called on startup to keep shared info searchable.

    Args:
        root_path: Project root path

    Returns:
        True if indexing succeeded (partial success counts as True)
    """
    available = await is_available()
    if not available:
        logger.info("[OpenViking] Not available, skipping enterprise index")
        return False

    enterprise_dir = _find_enterprise_dir(root_path)
    if enterprise_dir is None:
        logger.info("[OpenViking] enterprise_info directory not found, skipping")
        return False

    success_count = 0
    fail_count = 0

    for md_path in enterprise_dir.rglob("*.md"):
        if md_path.name.startswith("."):
            continue
        try:
            content = md_path.read_text(encoding="utf-8", errors="replace")
            resp = await _get_client().post(
                "/api/v1/skills",
                json={"data": content, "wait": False},
                headers={"X-OpenViking-Agent": "enterprise"},
                timeout=OPENVIKING_TIMEOUT * 2,
            )
            if resp.status_code < 300:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            logger.debug(f"[OpenViking] Failed to index {md_path}: {e}")
            fail_count += 1

    logger.info(f"[OpenViking] Indexed enterprise_info: {success_count} succeeded, {fail_count} failed")
    return fail_count == 0

async def search_skills(
    query: str,
    top_k: int = 5,
    grep_filter: str | None = None,
) -> list[str]:
    """Semantic search over installed skills to find relevant skills."""
    if not await is_available():
        return []
    try:
        body: dict = {
            "query": query,
            "target_uri": _SKILLS_SCOPE,
            "limit": top_k,
            "score_threshold": OPENVIKING_SCORE_THRESHOLD,
        }
        if grep_filter:
            body["grep_filter"] = grep_filter
        resp = await _get_client().post("/api/v1/search/find", json=body, timeout=OPENVIKING_TIMEOUT)
        if resp.status_code != 200:
            return []
        memories = resp.json().get("result", {}).get("memories", [])
        return [m.get("content") or m.get("abstract") or "" for m in memories if m.get("content") or m.get("abstract")]
    except httpx.ConnectError:
        _invalidate_availability_cache()
        return []
    except Exception as e:
        logger.debug(f"[OpenViking] search_skills failed: {e}")
        return []


async def index_all_skills(root_path: Path) -> bool:
    """Index all installed skills' SKILL.md to shared skills scope."""
    if not await is_available():
        return False

    skills_dir = _find_skills_dir(root_path)
    if skills_dir is None:
        logger.debug("[OpenViking] skills directory not found, skipping")
        return False

    skill_files = []
    for entry in skills_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            for name in ("SKILL.md", "skill.md"):
                if (entry / name).exists():
                    skill_files.append(entry / name)
                    break
        elif entry.suffix == ".md":
            skill_files.append(entry)

    success = True
    for skill_file in skill_files:
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
            resp = await _get_client().post(
                "/api/v1/skills",
                json={"data": content, "wait": False},
                headers={"X-OpenViking-Agent": "skills"},
                timeout=OPENVIKING_TIMEOUT * 2,
            )
            if resp.status_code >= 300:
                success = False
        except Exception as e:
            logger.debug(f"[OpenViking] index_all_skills error on {skill_file}: {e}")
            success = False

    logger.info(f"[OpenViking] Indexed {len(skill_files)} skill(s)")
    return success

async def index_all_agents(clawith_data_root: Path) -> bool:
    """
    Index all agents' core documents (soul.md, memory.md, focus.md, reflections.md)
    into a shared agent_docs scope for cross-agent semantic search.

    This allows querying across all agents for knowledge and patterns.

    Args:
        clawith_data_root: Clawith data root, typically ~/.clawith/data

    Returns:
        True if indexing succeeded (partial success counts as True)
    """
    available = await is_available()
    if not available:
        logger.info("[OpenViking] Not available, skipping agents index")
        return False

    # Search from ~/.clawith recursively to catch any agent document locations
    root = Path(os.path.expanduser("~/.clawith"))
    if not root.exists():
        logger.info("[OpenViking] ~/.clawith directory not found, skipping")
        return False

    success_count = 0
    fail_count = 0
    target_files = {"soul.md", "memory.md", "focus.md", "reflections.md"}

    # Use rglob to find all target files anywhere under ~/.clawith
    for target in target_files:
        for doc_path in root.rglob(target):
            if not doc_path.is_file():
                continue
            # Skip files inside virtual environments or node_modules
            if ".venv" in str(doc_path) or "node_modules" in str(doc_path):
                continue
            try:
                # Extract agent_id from path components (UUID-like directories)
                doc_agent_id = "unknown"
                for part in doc_path.parts:
                    if len(part) == 36 and part.count('-') == 4:
                        doc_agent_id = part
                        break
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                resp = await _get_client().post(
                    "/api/v1/skills",
                    json={"data": content, "wait": False},
                    headers={"X-OpenViking-Agent": doc_agent_id},
                    timeout=OPENVIKING_TIMEOUT * 2,
                )
                if resp.status_code < 300:
                    success_count += 1
                else:
                    fail_count += 1
                    logger.debug(f"[OpenViking] index_all_agents: {doc_path.name} -> {resp.status_code}")
            except Exception as e:
                logger.debug(f"[OpenViking] Failed to index agent doc {doc_path}: {e}")
                fail_count += 1

    logger.info(f"[OpenViking] Indexed all agents: {success_count} succeeded, {fail_count} failed")
    return fail_count == 0

async def search_all_agents(
    query: str,
    top_k: int = 5,
    grep_filter: str | None = None,
    agent_id: str | None = None,
) -> list[dict]:
    """
    Search across all agents' indexed documents in agent_docs scope.

    Args:
        query: Search query text
        top_k: Number of top results to return
        grep_filter: Optional keyword filter to filter documents before search
        agent_id: Optional filter to only search within a specific agent

    Returns:
        List of results with "path", "content", "score", "metadata" keys
    """
    available = await is_available()
    if not available:
        return []

    try:
        # Search across all agent scopes using a wildcard-style URI
        body: dict = {
            "query": query,
            "target_uri": _CLAWITH_SCOPE,
            "limit": top_k,
            "score_threshold": OPENVIKING_SCORE_THRESHOLD,
        }
        if grep_filter:
            body["grep_filter"] = grep_filter

        resp = await _get_client().post("/api/v1/search/find", json=body, timeout=OPENVIKING_TIMEOUT)
        if resp.status_code != 200:
            return []
        memories = resp.json().get("result", {}).get("memories", [])
        snippets = []
        for m in memories:
            content = m.get("content") or m.get("abstract") or ""
            if content:
                snippets.append({"content": content.strip(), "path": m.get("uri", "")})
        # Filter by agent_id if requested
        if agent_id:
            snippets = [s for s in snippets if agent_id in s.get("path", "")]
        return snippets
    except Exception as e:
        logger.debug(f"[OpenViking] search_all_agents failed: {e}")
        return []
