"""Adapter name registry (server-side).

Real adapter logic lives on the bridge (bridge/clawith_bridge/adapters/).
Server only knows the set of names it will accept in `session.start.adapter`
and the param schemas used for UI validation.
"""

from __future__ import annotations

from typing import Any

# Known adapter names. Add to this list when a new adapter ships on
# the bridge side. Unknown adapters are rejected at session start.
KNOWN_ADAPTERS: set[str] = {
    "claude_code",
    "hermes",
    "openclaw",
}

# Minimal param schema per adapter. Keys are the `params` field on
# SessionStartFrame. Used only for validation / UI hints.
ADAPTER_PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    "claude_code": {
        "model": {"type": "string", "description": "Claude model override, e.g. claude-opus-4-7"},
        "allowed_tools": {"type": "array", "description": "Whitelist of tool names the CLI may use"},
        "max_turns": {"type": "integer", "description": "Max tool-loop turns"},
    },
    "hermes": {
        "endpoint": {"type": "string", "description": "Hermes daemon URL override"},
    },
    "openclaw": {
        # OpenClaw historically accepts a bare prompt; no structured params.
    },
}


def is_known_adapter(name: str) -> bool:
    return name in KNOWN_ADAPTERS
