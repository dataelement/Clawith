"""Build a structured skill map for the agent detail API.

Returns a dict of skill entries, each with optional sub-items
from reference.json files.
"""
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import settings

# Simple TTL cache: (agent_id -> (timestamp, result))
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60  # seconds


def _parse_frontmatter_fields(content: str, fallback_name: str) -> dict:
    """Extract name, description from YAML frontmatter."""
    name = fallback_name.replace("_", " ").replace("-", " ")
    description = ""

    stripped = content.strip()
    if stripped.startswith("---"):
        end = stripped.find("---", 3)
        if end != -1:
            for line in stripped[3:end].strip().split("\n"):
                line = line.strip()
                if line.lower().startswith("name:"):
                    val = line[5:].strip().strip('"').strip("'")
                    if val:
                        name = val
                elif line.lower().startswith("description:"):
                    val = line[12:].strip().strip('"').strip("'")
                    if val:
                        description = val[:200]
    return {"name": name, "description": description}


def _load_reference_json(agent_skills_dir: Path, skill_name: str) -> dict | None:
    """Load reference.json with fallback chain: agent-level -> global."""
    # 1. Agent-level override
    agent_ref = agent_skills_dir / skill_name / "reference.json"
    if agent_ref.exists():
        try:
            return json.loads(agent_ref.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2. Global fallback (only for known skill names with global source)
    global_dir = Path(settings.AGENCY_AGENTS_DIR)
    global_ref = global_dir / "reference.json"
    if global_ref.exists():
        try:
            return json.loads(global_ref.read_text(encoding="utf-8"))
        except Exception:
            pass

    return None


def get_skill_map(agent_id: UUID) -> dict[str, Any]:
    """Build skill map for an agent.

    Returns dict like:
    {
        "role": {
            "has_sub_items": True,
            "items": [{"key": "frontend-developer", "name": "Frontend Developer", ...}]
        },
        "data-analysis": {
            "has_sub_items": False,
            "description": "Data interpretation..."
        }
    }
    """
    cache_key = str(agent_id)
    now = time.time()

    # Check cache
    if cache_key in _cache:
        ts, result = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return result

    from app.services.agent_context import TOOL_WORKSPACE, PERSISTENT_DATA

    skill_map: dict[str, Any] = {}

    for ws_root in [TOOL_WORKSPACE / str(agent_id), PERSISTENT_DATA / str(agent_id)]:
        skills_dir = ws_root / "skills"
        if not skills_dir.exists():
            continue

        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith("."):
                continue

            skill_key = entry.stem if entry.is_file() else entry.name

            # Skip if already seen (dedup across workspaces)
            if skill_key in skill_map:
                continue

            # Folder-based skill
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if not skill_md.exists():
                    skill_md = entry / "skill.md"

                meta = {"name": skill_key, "description": ""}
                if skill_md.exists():
                    try:
                        content = skill_md.read_text(encoding="utf-8", errors="replace")
                        meta = _parse_frontmatter_fields(content, skill_key)
                    except Exception:
                        pass

                # Check for reference.json (sub-items)
                ref_data = _load_reference_json(skills_dir, skill_key)
                if ref_data:
                    items = [
                        {
                            "key": k,
                            "name": v.get("name", k),
                            "emoji": v.get("emoji", ""),
                            "description": v.get("description", "")[:200],
                        }
                        for k, v in ref_data.items()
                    ]
                    skill_map[skill_key] = {
                        "has_sub_items": True,
                        "description": meta["description"],
                        "items": items,
                    }
                else:
                    skill_map[skill_key] = {
                        "has_sub_items": False,
                        "description": meta["description"],
                    }

            # Flat file skill
            elif entry.suffix == ".md" and entry.is_file():
                try:
                    content = entry.read_text(encoding="utf-8", errors="replace")
                    meta = _parse_frontmatter_fields(content, entry.stem)
                except Exception:
                    meta = {"name": entry.stem, "description": ""}

                skill_map[skill_key] = {
                    "has_sub_items": False,
                    "description": meta["description"],
                }

    # Cache result
    _cache[cache_key] = (now, skill_map)
    return skill_map
