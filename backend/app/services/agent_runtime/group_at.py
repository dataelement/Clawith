"""Group-only structured mention intent used before a natural final response."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
import uuid


AT_TOOL_NAME = "at"
MAX_GROUP_AT_PARTICIPANTS = 100

AT_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": AT_TOOL_NAME,
        "description": (
            "Set the complete list of Group Agents that must be visibly mentioned "
            "and woken by the next final public reply. This only stages routing and "
            "does not send a message or finish the Run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "participant_ids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": MAX_GROUP_AT_PARTICIPANTS,
                    "uniqueItems": True,
                }
            },
            "required": ["participant_ids"],
            "additionalProperties": False,
        },
    },
}


class GroupAtArgumentsError(ValueError):
    """The model supplied an invalid group ``at`` target set."""


def group_at_tool_definition() -> dict[str, Any]:
    return deepcopy(AT_TOOL_DEFINITION)


def parse_group_at_participant_ids(arguments: Mapping[str, object]) -> tuple[str, ...]:
    unsupported = set(arguments) - {"participant_ids"}
    if unsupported:
        raise GroupAtArgumentsError(
            "`at` contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported))
        )
    raw_ids = arguments.get("participant_ids")
    if not isinstance(raw_ids, list):
        raise GroupAtArgumentsError("`at.participant_ids` must be an array")
    if len(raw_ids) > MAX_GROUP_AT_PARTICIPANTS:
        raise GroupAtArgumentsError(
            f"`at.participant_ids` may contain at most {MAX_GROUP_AT_PARTICIPANTS} entries"
        )
    normalized: list[str] = []
    for raw_id in raw_ids:
        if not isinstance(raw_id, str):
            raise GroupAtArgumentsError(
                "`at.participant_ids` must contain only UUID strings"
            )
        try:
            participant_id = str(uuid.UUID(raw_id))
        except ValueError as exc:
            raise GroupAtArgumentsError(
                "`at.participant_ids` must contain only valid UUID strings"
            ) from exc
        if participant_id in normalized:
            raise GroupAtArgumentsError("`at.participant_ids` must contain unique UUIDs")
        normalized.append(participant_id)
    return tuple(normalized)


__all__ = [
    "AT_TOOL_DEFINITION",
    "AT_TOOL_NAME",
    "GroupAtArgumentsError",
    "MAX_GROUP_AT_PARTICIPANTS",
    "group_at_tool_definition",
    "parse_group_at_participant_ids",
]
