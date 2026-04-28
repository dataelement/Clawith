"""Truncate large tool results to disk with an in-context pointer.

When a tool returns ~50 KB of jina_search hits or a 30-page PDF extract,
sending it verbatim into the LLM history burns tokens for content the model
will only sample one slice of. This module spills oversized payloads to the
agent's workspace and replaces the in-context body with a head excerpt plus
a ``[truncated. Full output saved to ...]`` marker. The model is taught (in
the system prompt) to follow up with ``read_file`` for specific sections.

Why a "smart" head:
  - Naive ``content[:N]`` works for prose but ruins JSON-shape responses
    where the head is metadata (``query``, ``total_results``) and the
    payload is in a ``results``/``items``/``data`` array.
  - We try ``json.loads`` first; if the content is a dict with one of the
    well-known array keys, we keep the metadata and the first few items.
    Otherwise fall back to head-cut.

What's intentionally not handled:
  - Vision injection payloads (``list`` content) pass through unchanged.
    Truncating image_data markers would corrupt the multimodal block.
  - DB persistence already truncates to 500 chars (websocket.py +
    agent_tools.py + trigger_daemon.py). This module operates on the
    in-flight ``api_messages`` list, not the DB form.
  - Sandbox boundary: ``_tool_results/<call_id>.txt`` lives under the
    agent's ``AGENT_DATA_DIR / str(agent_id) /`` workspace — read_file
    already enforces this boundary, so cross-agent leak is prevented by
    the existing tool-level sandbox.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.token_tracker import estimate_tokens_from_chars

# Conservative initial threshold: 4000 estimated tokens (~12 KB at chars/3).
# Soft-start value to give the model time to learn the read_file follow-up
# pattern; can be tightened to ~2000 once telemetry confirms the model uses
# the marker reliably.
TOOL_RESULT_TOKEN_THRESHOLD = 4000

# Allowed characters for a call_id used as a filename. Anthropic returns
# ``toolu_<UUID>``; OpenAI returns ``call_<UUID>``; clawith synthesizes
# ``call_<DB-row-uuid>``. All three fit this set. Anything else (e.g. a
# prompt-injected ``../../etc/passwd``) gets slugged before reaching the
# filesystem.
_SAFE_CALL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_call_id(call_id: str) -> str:
    """Return a filesystem-safe version of ``call_id``.

    Provider-issued call_ids already match ``_SAFE_CALL_ID_RE`` and pass
    through unchanged. Hostile or malformed inputs (path separators, unicode,
    arbitrarily long) are replaced char-by-char and length-capped — defense
    in depth against a prompt-injection scenario where the model is coaxed
    into emitting a path-traversing ``tool_call_id``.
    """
    if not call_id:
        return "unknown"
    if _SAFE_CALL_ID_RE.match(call_id):
        return call_id
    slugged = re.sub(r"[^A-Za-z0-9_-]", "_", str(call_id))[:128]
    return slugged or "unknown"

# Hard char limit for the in-context head excerpt. Keeps the in-context
# payload small even for unusual content (e.g. one giant JSON string).
_HEAD_MAX_CHARS = TOOL_RESULT_TOKEN_THRESHOLD * 3  # ~12 KB

# JSON keys that conventionally hold the array payload (the "results" of a
# search, the "items" of a list endpoint, etc). Order matters — first hit wins.
_KNOWN_ARRAY_KEYS = ("results", "items", "data", "entries", "hits", "documents")

# How many array elements to keep when smart-heading a JSON-shape response.
_KEEP_ARRAY_ITEMS = 5


def maybe_truncate_tool_result(
    tool_content: str | list,
    *,
    call_id: str,
    agent_workspace: Path,
) -> str | list:
    """Return ``tool_content`` shortened with a marker, or unchanged.

    Args:
        tool_content: The raw tool result. ``str`` is the common case;
            ``list`` is multimodal vision-injected content (passed through).
        call_id: Tool call ID — used as the spill filename.
        agent_workspace: Path to the agent's workspace dir
            (``AGENT_DATA_DIR / str(agent_id)``). Spill goes under
            ``<workspace>/_tool_results/<call_id>.txt``.

    Returns:
        The original content if under threshold, or a head excerpt + marker
        string. List payloads always pass through.
    """
    # Multimodal content: image_data markers must stay structurally intact.
    if isinstance(tool_content, list):
        return tool_content

    if not isinstance(tool_content, str):
        # Defensive: unknown payload type — coerce and continue.
        tool_content = str(tool_content)

    est_tokens = estimate_tokens_from_chars(len(tool_content))
    if est_tokens <= TOOL_RESULT_TOKEN_THRESHOLD:
        return tool_content

    # Spill full content to disk. Sanitize the call_id and assert containment
    # so a hostile/malformed call_id can never write outside _tool_results/.
    safe_id = _safe_call_id(call_id)
    spill_root = (agent_workspace / "_tool_results").resolve()
    full_path = (spill_root / f"{safe_id}.txt").resolve()
    if not _is_within(full_path, spill_root):
        # Should be unreachable after slugging — defense in depth.
        logger.error(
            f"[tool-truncation] Refusing to spill {call_id!r} → {full_path} "
            f"(outside {spill_root}); falling back to inline head-cut."
        )
        head = _smart_head(tool_content, _HEAD_MAX_CHARS)
        return (
            head
            + f"\n\n[truncated. Full output ({est_tokens} tokens) "
            f"could not be spilled to disk — only this excerpt is available]"
        )

    try:
        spill_root.mkdir(parents=True, exist_ok=True)
        full_path.write_text(tool_content, encoding="utf-8")
    except OSError as e:
        logger.warning(
            f"[tool-truncation] Failed to spill {safe_id} to {full_path}: {e}; "
            "falling back to inline head-cut without spill."
        )
        head = _smart_head(tool_content, _HEAD_MAX_CHARS)
        return (
            head
            + f"\n\n[truncated. Full output ({est_tokens} tokens) "
            f"could not be spilled to disk — only this excerpt is available]"
        )

    head = _smart_head(tool_content, _HEAD_MAX_CHARS)
    return (
        head
        + f"\n\n[truncated. Full output ({est_tokens} tokens) saved to "
        f"_tool_results/{safe_id}.txt under your workspace — use the read_file "
        f"tool to retrieve specific sections]"
    )


def _is_within(path: Path, root: Path) -> bool:
    """True iff ``path`` is the same as ``root`` or nested below it.

    Both paths are expected to already be ``.resolve()``-d by the caller.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _smart_head(content: str, max_chars: int) -> str:
    """Best-effort excerpt that preserves structure when possible.

    JSON-shape: if the content parses as a dict with a known array key
    (``results``/``items``/``data``/...), keep the metadata + first 5 items.
    Otherwise fall back to a plain head cut.
    """
    if len(content) <= max_chars:
        return content

    # Try JSON-shape preservation
    stripped = content.lstrip()
    if stripped.startswith("{"):
        try:
            data: dict[str, Any] = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass
        else:
            if isinstance(data, dict):
                truncated = _truncate_json_dict(data)
                if truncated is not None:
                    rendered = json.dumps(truncated, ensure_ascii=False, indent=2)
                    if len(rendered) <= max_chars:
                        return rendered
                    # Even truncated JSON exceeds head budget → fall through.

    # Plain head cut at a word/line boundary if possible
    cut = content[:max_chars]
    last_newline = cut.rfind("\n")
    if last_newline > max_chars * 0.8:
        cut = cut[:last_newline]
    return cut


def _truncate_json_dict(data: dict[str, Any]) -> dict[str, Any] | None:
    """If ``data`` has a known array key, return a dict with metadata
    preserved and that array trimmed to ``_KEEP_ARRAY_ITEMS`` entries.
    Returns None if no known array shape is detected.
    """
    for key in _KNOWN_ARRAY_KEYS:
        value = data.get(key)
        if isinstance(value, list) and len(value) > _KEEP_ARRAY_ITEMS:
            trimmed = dict(data)
            kept = value[:_KEEP_ARRAY_ITEMS]
            trimmed[key] = kept
            trimmed[f"_truncated_{key}_count"] = len(value) - _KEEP_ARRAY_ITEMS
            return trimmed
    return None
