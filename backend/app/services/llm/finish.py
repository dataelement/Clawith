"""Finish-tool protocol helpers for agent execution loops."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
import uuid


FINISH_TOOL_NAME = "finish"
MAX_GROUP_FINISH_MENTIONS = 100

FINISH_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FINISH_TOOL_NAME,
        "description": (
            "Finish the current Run and send the final user-facing response only "
            "after the user's requested outcome is complete and all required "
            "verification has passed. Never use finish for a progress update or an "
            "incomplete result. Put the full answer the user should see in content, "
            "and do not call any other tools in the same response."
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
            "additionalProperties": False,
        },
    },
}


def group_finish_tool_definition() -> dict[str, Any]:
    """Return the shared finish schema with the Group-only mention field."""
    definition = deepcopy(FINISH_TOOL_DEFINITION)
    parameters = definition["function"]["parameters"]
    parameters["properties"]["mention_participant_ids"] = {
        "type": "array",
        "description": (
            "Optional stable participant UUIDs for Agent members to @ in this final "
            "public group reply. Each mentioned Agent is woken and must reply "
            "publicly in the same group session. Use this only for a concrete question, "
            "request, or responsibility that requires a new reply now; it is not limited "
            "to ownership transfer. In every case where the Agent does not need to "
            "answer again now, regardless of topic, wording, tone, or intent, omit its "
            "ID and write the display name without @. This includes, but is not limited "
            "to, greetings, thanks, acknowledgments, introductions, compliments, status "
            "statements, summaries, references, or future collaboration. Query "
            "group members when an ID is unknown, then put the "
            "returned IDs in this field in the same finish call. In content, write "
            "each target as the literal @display name and state the concrete question or request; "
            "include only the public group message and never explain IDs, tools, "
            "routing, Runtime, or child Runs. Textual @names in content do not wake "
            "Agents; never infer IDs from display names."
        ),
        "items": {"type": "string", "format": "uuid"},
        "maxItems": MAX_GROUP_FINISH_MENTIONS,
        "uniqueItems": True,
    }
    return definition

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

_EXPLICIT_GROUP_HANDOFF_PATTERNS = (
    re.compile(
        r"(?:^|[\n\r\-—:：])\s*(?:#+\s*)?"
        r"(?:handoff|hand-off)(?:\s+(?:target|目标))?\s*"
        r"(?:to|给|至|->|→|:|：)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:交接|移交|转交)(?:任务|工作|责任|目标|后续)?\s*(?:给|至|:|：)"),
    re.compile(
        r"@[A-Za-z0-9_.\-\u4e00-\u9fff]{1,100}\s+"
        r"(?:can|should|must|will)\s+(?:continue|take over|proceed)",
        re.IGNORECASE,
    ),
)


def content_claims_group_handoff(content: str) -> bool:
    """Detect an explicit public handoff claim without resolving its target.

    This is a protocol consistency guard only. It never parses a target identity
    or turns text into routing data; a valid handoff still requires stable IDs.
    """
    return any(pattern.search(content) for pattern in _EXPLICIT_GROUP_HANDOFF_PATTERNS)


@dataclass(frozen=True)
class FinishCall:
    """Parsed finish tool call."""

    call_id: str
    content: str
    mention_participant_ids: tuple[str, ...] = ()
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


def find_finish_call(
    tool_calls: list[dict] | None,
    *,
    allow_group_mentions: bool = False,
) -> FinishCall | None:
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

        unsupported = set(args) - {"content", "mention_participant_ids"}
        if unsupported:
            return FinishCall(
                call_id=call_id,
                content="",
                error=(
                    "`finish` contains unsupported fields: "
                    + ", ".join(sorted(str(field) for field in unsupported))
                    + "."
                ),
            )

        raw_mentions = args.get("mention_participant_ids")
        if raw_mentions is not None and not allow_group_mentions:
            return FinishCall(
                call_id=call_id,
                content="",
                error=(
                    "`mention_participant_ids` is available only to a validated "
                    "Group Agent Run."
                ),
            )
        if raw_mentions is None:
            mention_ids: tuple[str, ...] = ()
        elif not isinstance(raw_mentions, list):
            return FinishCall(
                call_id=call_id,
                content="",
                error="`mention_participant_ids` must be an array of participant UUID strings.",
            )
        elif len(raw_mentions) > MAX_GROUP_FINISH_MENTIONS:
            return FinishCall(
                call_id=call_id,
                content="",
                error=(
                    "`mention_participant_ids` may contain at most "
                    f"{MAX_GROUP_FINISH_MENTIONS} entries."
                ),
            )
        else:
            normalized: list[str] = []
            for raw_participant_id in raw_mentions:
                if not isinstance(raw_participant_id, str):
                    return FinishCall(
                        call_id=call_id,
                        content="",
                        error=(
                            "`mention_participant_ids` must contain only participant "
                            "UUID strings."
                        ),
                    )
                try:
                    participant_id = str(uuid.UUID(raw_participant_id))
                except ValueError:
                    return FinishCall(
                        call_id=call_id,
                        content="",
                        error=(
                            "`mention_participant_ids` must contain only valid "
                            "participant UUID strings."
                        ),
                    )
                if participant_id not in normalized:
                    normalized.append(participant_id)
            mention_ids = tuple(normalized)

        if (
            allow_group_mentions
            and not mention_ids
            and content_claims_group_handoff(content)
        ):
            return FinishCall(
                call_id=call_id,
                content="",
                error=(
                    "`content` explicitly claims a Group handoff, but "
                    "`mention_participant_ids` is empty. If another Agent must "
                    "continue, call `group_query_members` and retry `finish` with "
                    "every stable target participant ID. Otherwise remove the "
                    "handoff claim. Text alone never routes work."
                ),
            )

        return FinishCall(
            call_id=call_id,
            content=content,
            mention_participant_ids=mention_ids,
        )

    return None
