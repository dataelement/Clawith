"""One-version decoder for legacy finish calls and checkpoint repair messages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
import uuid


FINISH_TOOL_NAME = "finish"
MAX_GROUP_FINISH_MENTIONS = 100

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


def parse_legacy_finish_content(
    content: str,
    *,
    allow_group_mentions: bool = False,
) -> FinishCall | None:
    """Decode only unmistakable legacy finish JSON from Assistant content.

    A plain ``{"content": ...}`` object may be a user-requested JSON answer, so
    it remains visible. The legacy group field or an explicit finish envelope
    is required before content is interpreted as Runtime control data.
    """
    try:
        payload = json.loads(content.strip())
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    arguments: Any
    if "mention_participant_ids" in payload:
        arguments = payload
    elif payload.get("name") == FINISH_TOOL_NAME and "arguments" in payload:
        if set(payload) - {"id", "name", "arguments"}:
            return None
        arguments = payload.get("arguments")
    else:
        function = payload.get("function")
        if (
            isinstance(function, dict)
            and function.get("name") == FINISH_TOOL_NAME
            and not (set(payload) - {"id", "type", "function"})
        ):
            arguments = function.get("arguments")
        else:
            return None

    return find_finish_call(
        [
            {
                "id": str(payload.get("id") or "legacy_finish_content"),
                "type": "function",
                "function": {
                    "name": FINISH_TOOL_NAME,
                    "arguments": arguments,
                },
            }
        ],
        allow_group_mentions=allow_group_mentions,
    )
