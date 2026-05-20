"""Finish-tool protocol helpers for agent execution loops."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


FINISH_TOOL_NAME = "finish"

FINISH_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FINISH_TOOL_NAME,
        "description": (
            "Finish the current turn and send the final user-facing response. "
            "You MUST call this tool exactly when you are ready to stop. Put the full answer "
            "the user should see in content. Do not call any other tools in the same response."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The final response to show to the user.",
                },
            },
            "required": ["content"],
        },
    },
}

FINISH_TOOL_SEED: dict[str, Any] = {
    "name": FINISH_TOOL_NAME,
    "display_name": "Finish",
    "description": FINISH_TOOL_DEFINITION["function"]["description"],
    "category": "system",
    "icon": "check",
    "is_default": True,
    "parameters_schema": FINISH_TOOL_DEFINITION["function"]["parameters"],
    "config": {},
    "config_schema": {},
}

FINISH_PROTOCOL_REMINDER = (
    "Your previous response did not call any tool, so this turn is not finished. "
    "You must either call another available tool if more work is needed, or call "
    "`finish` with the complete user-facing answer in `content`. Do not answer in plain text."
)


@dataclass(frozen=True)
class FinishCall:
    """Parsed finish tool call."""

    call_id: str
    content: str
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None


def parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    """Parse OpenAI-style function arguments into a dict."""
    if raw_args is None or raw_args == "":
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        parsed = json.loads(raw_args)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def find_finish_call(tool_calls: list[dict] | None) -> FinishCall | None:
    """Return the first finish call from a tool call list, if present."""
    for tc in tool_calls or []:
        fn = tc.get("function") or {}
        if (fn.get("name") or "").strip() != FINISH_TOOL_NAME:
            continue

        call_id = tc.get("id", "")
        try:
            args = parse_tool_arguments(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            return FinishCall(
                call_id=call_id,
                content="",
                error="`finish` arguments must be valid JSON with a required string field `content`.",
            )

        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return FinishCall(
                call_id=call_id,
                content="",
                error="`finish` requires a non-empty string field `content`.",
            )

        return FinishCall(call_id=call_id, content=content)

    return None
