"""Checkpoint-safe Tool repair episode transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from app.services.agent_runtime.state import JsonObject

SAME_FINGERPRINT_FAILURE_LIMIT = 10
TOOL_EPISODE_FAILURE_LIMIT = 10
_REPAIRABLE_MODEL_ACTIONS = frozenset(
    {"repair_arguments", "choose_other_tool"}
)


class ToolRepairBudgetError(ValueError):
    """Checkpoint repair episode state is malformed."""


@dataclass(frozen=True, slots=True)
class ToolRepairTransition:
    episodes: JsonObject
    counted: bool = False
    reset_tool_name: str | None = None
    pause_reason: str | None = None
    paused_tool_name: str | None = None


def _text(value: object, *, field: str, max_length: int = 255) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolRepairBudgetError(f"{field} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ToolRepairBudgetError(f"{field} exceeds its length limit")
    return normalized


def _parse_episodes(raw: object) -> dict[str, dict]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise ToolRepairBudgetError("tool repair episodes require version 1")
    by_tool = raw.get("by_tool")
    if not isinstance(by_tool, Mapping) or len(by_tool) > 256:
        raise ToolRepairBudgetError("tool repair episodes by_tool is invalid")
    parsed: dict[str, dict] = {}
    for raw_tool_name, raw_episode in by_tool.items():
        tool_name = _text(raw_tool_name, field="tool_name", max_length=200)
        if not isinstance(raw_episode, Mapping):
            raise ToolRepairBudgetError("tool repair episode must be an object")
        episode = dict(raw_episode)
        for field in ("total_failures", "same_fingerprint_failures"):
            value = episode.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ToolRepairBudgetError(f"repair episode {field} is invalid")
        _text(episode.get("episode_id"), field="episode_id")
        _text(episode.get("last_fingerprint"), field="last_fingerprint")
        _text(
            episode.get("last_call_instance_id"),
            field="last_call_instance_id",
        )
        updated_at = episode.get("updated_at_model_step")
        if (
            isinstance(updated_at, bool)
            or not isinstance(updated_at, int)
            or updated_at < 0
        ):
            raise ToolRepairBudgetError(
                "repair episode updated_at_model_step is invalid"
            )
        parsed[tool_name] = episode
    return parsed


def _json(by_tool: Mapping[str, dict]) -> JsonObject:
    return {
        "version": 1,
        "by_tool": {
            tool_name: dict(episode)
            for tool_name, episode in sorted(by_tool.items())
        },
    }


def _fingerprint(message: Mapping[str, object]) -> str:
    explicit = message.get("failure_fingerprint")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:255]
    payload = {
        "error_code": message.get("error_code"),
        "model_action": message.get("model_action"),
        "content": str(message.get("content") or "")[:2000],
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def apply_tool_result(
    raw_episodes: object,
    message: Mapping[str, object],
    *,
    model_step: int,
) -> ToolRepairTransition:
    """Apply one model-visible Tool Result without touching other budgets."""
    if isinstance(model_step, bool) or not isinstance(model_step, int) or model_step < 0:
        raise ToolRepairBudgetError("model_step must be a non-negative integer")
    by_tool = _parse_episodes(raw_episodes)
    tool_name = message.get("name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return ToolRepairTransition(episodes=_json(by_tool))
    tool_name = tool_name.strip()
    status = message.get("execution_status")
    if status == "succeeded":
        reset = by_tool.pop(tool_name, None) is not None
        return ToolRepairTransition(
            episodes=_json(by_tool),
            reset_tool_name=tool_name if reset else None,
        )
    if (
        status != "failed"
        or message.get("model_action") not in _REPAIRABLE_MODEL_ACTIONS
        or message.get("side_effect_state") != "none"
    ):
        return ToolRepairTransition(episodes=_json(by_tool))

    call_instance_id = _text(
        message.get("tool_call_id") or message.get("call_instance_id"),
        field="call_instance_id",
    )
    fingerprint = _fingerprint(message)
    prior = by_tool.get(tool_name)
    total_failures = int(prior["total_failures"]) + 1 if prior else 1
    same_failures = (
        int(prior["same_fingerprint_failures"]) + 1
        if prior and prior["last_fingerprint"] == fingerprint
        else 1
    )
    episode_id = (
        str(prior["episode_id"])
        if prior
        else "episode:"
        + hashlib.sha256(
            f"{tool_name}:{call_instance_id}".encode()
        ).hexdigest()[:24]
    )
    by_tool[tool_name] = {
        "tool_name": tool_name,
        "episode_id": episode_id,
        "total_failures": total_failures,
        "last_fingerprint": fingerprint,
        "same_fingerprint_failures": same_failures,
        "last_call_instance_id": call_instance_id,
        "updated_at_model_step": model_step,
    }
    pause_reason = (
        "tool_repair_same_fingerprint_limit_reached"
        if same_failures >= SAME_FINGERPRINT_FAILURE_LIMIT
        else "tool_repair_episode_limit_reached"
        if total_failures >= TOOL_EPISODE_FAILURE_LIMIT
        else None
    )
    return ToolRepairTransition(
        episodes=_json(by_tool),
        counted=True,
        pause_reason=pause_reason,
        paused_tool_name=tool_name if pause_reason is not None else None,
    )


def reset_tool_repair_episodes(
    raw_episodes: object,
    *,
    tool_name: str | None = None,
) -> JsonObject:
    by_tool = _parse_episodes(raw_episodes)
    if tool_name is None:
        by_tool.clear()
    else:
        by_tool.pop(_text(tool_name, field="tool_name", max_length=200), None)
    return _json(by_tool)


__all__ = [
    "SAME_FINGERPRINT_FAILURE_LIMIT",
    "TOOL_EPISODE_FAILURE_LIMIT",
    "ToolRepairBudgetError",
    "ToolRepairTransition",
    "apply_tool_result",
    "reset_tool_repair_episodes",
]
