"""Tool call result auto-persistence module.

Automatically saves full tool results to workspace/tool_artifacts/ so the LLM
can retrieve them later via read_file / search_files / list_files.

Design: zero new tools, no cache, minimal code, all failures are silent.
"""

import json
import hashlib
import uuid
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
MAX_RESULT_SIZE = 100_000  # 100 KB — skip saving oversized results


def _category(tool_name: str) -> str:
    return CATEGORY_MAP.get(tool_name, "misc")


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
        # Unique filename: timestamp + short random id
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        filename = f"{ts}_{short_id}.json"

        data = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
        (d / filename).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"{ARTIFACTS_DIR}/{category}/{filename}"
    except Exception:
        return None
