"""Build a flat, colon-keyed skill map by recursively scanning skills/**/*.md.

Each .md file with a `name` field in YAML frontmatter is a skill entry.
The colon key is derived from the folder path + slugified name.
"""
import logging
import re
import time
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60


def slugify(name: str) -> str:
    """Convert 'Frontend Developer' -> 'frontend-developer'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract name, description, emoji from YAML frontmatter.

    Returns dict with keys present in frontmatter. Missing keys are omitted.
    """
    stripped = content.strip()
    if not stripped.startswith("---"):
        return {}
    end = stripped.find("---", 3)
    if end == -1:
        return {}
    result = {}
    for line in stripped[3:end].strip().split("\n"):
        line = line.strip()
        for field in ("name", "description", "emoji"):
            if line.lower().startswith(f"{field}:"):
                val = line[len(field) + 1:].strip().strip('"').strip("'")
                if val:
                    result[field] = val if field != "description" else val[:200]
                break
    return result


def _build_colon_key(rel_path: Path, slugified_name: str) -> str:
    """Build colon key from folder segments + slugified name, with dedup."""
    segments = list(rel_path.parent.parts)  # folder segments, exclude filename
    if segments and segments[-1] == slugified_name:
        pass  # dedup: last folder == slug, don't append
    else:
        segments.append(slugified_name)
    return ":".join(segments)


def _scan_skills_dir(skills_dir: Path) -> dict[str, dict[str, str]]:
    """Scan a skills directory recursively, return flat colon-keyed map."""
    entries: dict[str, dict[str, str]] = {}

    if not skills_dir.exists():
        return entries

    for md_file in sorted(skills_dir.rglob("*.md")):
        if md_file.name.startswith("."):
            continue

        try:
            with open(md_file, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(1024)
        except Exception:
            continue

        fm = parse_frontmatter(head)
        name = fm.get("name")
        if not name:
            continue

        slug = slugify(name)
        rel = md_file.relative_to(skills_dir)
        key = _build_colon_key(rel, slug)

        if key in entries:
            logger.warning(f"Skill key collision '{key}': keeping '{entries[key]['file']}', skipping '{rel}'")
            continue

        entries[key] = {
            "name": name,
            "description": fm.get("description", ""),
            "emoji": fm.get("emoji", ""),
            "file": str(rel),
        }

    return entries


def get_skill_map(agent_id: UUID) -> dict[str, Any]:
    """Build flat colon-keyed skill map for an agent. Cached with 60s TTL."""
    cache_key = str(agent_id)
    now = time.time()

    if cache_key in _cache:
        ts, result = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return result

    from app.services.agent_context import TOOL_WORKSPACE, PERSISTENT_DATA

    merged: dict[str, dict[str, str]] = {}

    for ws_root in [TOOL_WORKSPACE / str(agent_id), PERSISTENT_DATA / str(agent_id)]:
        skills_dir = ws_root / "skills"
        scanned = _scan_skills_dir(skills_dir)
        for key, entry in scanned.items():
            if key not in merged:
                merged[key] = entry

    _cache[cache_key] = (now, merged)
    return merged


def get_skill_map_for_api(agent_id: UUID) -> dict[str, Any]:
    """Return skill map without file paths (safe for API response)."""
    full = get_skill_map(agent_id)
    return {
        key: {k: v for k, v in entry.items() if k != "file"}
        for key, entry in full.items()
    }


def invalidate_cache(agent_id: UUID) -> None:
    """Remove cached skill map for an agent."""
    _cache.pop(str(agent_id), None)
