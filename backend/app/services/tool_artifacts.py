"""Tool call result auto-persistence module.

Automatically saves full tool results to workspace/tool_artifacts/ so the LLM
can retrieve them later via read_file / search_files / list_files.

Design: zero new tools, minimal code, all failures are silent.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime

# ── Tool → sub-directory mapping ────────────────────────────────
CATEGORY_MAP: dict[str, str] = {
    "web_search": "search",
    "jina_search": "search",
    "bing_search": "search",
    "jina_read": "web_pages",
    "read_webpage": "web_pages",
    "execute_code": "code_outputs",
    "execute_code_e2b": "code_outputs",
}

ARTIFACTS_DIR = "tool_artifacts"

# ── Cache TTL (seconds) ────────────────────────────────────────
CACHE_TTL: dict[str, int] = {
    "web_search": 3600,     # 1 hour
    "jina_search": 3600,
    "jina_read": 86400,     # 24 hours
    "read_webpage": 86400,
}

# ── Size limit ─────────────────────────────────────────────────
MAX_RESULT_SIZE = 100_000  # 100 KB — skip saving if result exceeds this


def _category(tool_name: str) -> str:
    return CATEGORY_MAP.get(tool_name, "misc")


def _cache_key(tool_name: str, arguments: dict) -> str:
    """Deterministic hash from tool name + arguments."""
    raw = json.dumps({"t": tool_name, "a": arguments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _ensure_dir(ws: Path, category: str) -> Path:
    d = ws / ARTIFACTS_DIR / category
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_artifact(
    ws: Path,
    tool_name: str,
    arguments: dict,
    result: str,
) -> str | None:
    """Save a tool result to disk. Returns relative path or None on failure."""
    try:
        if len(result) > MAX_RESULT_SIZE:
            return None
        category = _category(tool_name)
        d = _ensure_dir(ws, category)
        key = _cache_key(tool_name, arguments)
        filename = f"{key}.json"
        filepath = d / filename

        # Skip if file already exists (cache hit scenario — avoid TTL refresh)
        if filepath.exists():
            return f"{ARTIFACTS_DIR}/{category}/{filename}"

        data = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"{ARTIFACTS_DIR}/{category}/{filename}"
    except Exception:
        return None


def check_cache(ws: Path, tool_name: str, arguments: dict) -> str | None:
    """Return cached result if present and within TTL. None otherwise."""
    try:
        ttl = CACHE_TTL.get(tool_name)
        if not ttl:
            return None
        category = _category(tool_name)
        filepath = ws / ARTIFACTS_DIR / category / f"{_cache_key(tool_name, arguments)}.json"
        if not filepath.exists():
            return None
        data = json.loads(filepath.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["timestamp"])
        if (datetime.now() - ts).total_seconds() > ttl:
            return None
        return data["result"]
    except Exception:
        return None
