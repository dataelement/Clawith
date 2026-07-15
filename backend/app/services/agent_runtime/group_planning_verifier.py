"""Receipt-backed completion checks for delegated group Planning steps."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from app.services.agent_runtime.node_executor import (
    DeterministicRuntimeVerifier,
    RuntimeVerifier,
    VerificationResult,
)
from app.services.agent_runtime.state import RuntimeContext, RuntimeGraphState


_ARTIFACT_WRITE_TOOL = "group_write_workspace_file"
_ROOT_RUN_ID_FIELD = "planning_root_run_id"
_STEP_ID_FIELD = "planning_step_id"
_REQUIRED_TOOLS_FIELD = "planning_required_tool_names"
_REQUIRED_ARTIFACTS_FIELD = "planning_required_artifact_paths"


@dataclass(frozen=True, slots=True)
class _PlanningRequirements:
    root_run_id: str
    step_id: str
    tool_names: tuple[str, ...]
    artifact_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SuccessfulToolCall:
    name: str
    arguments: Mapping[str, object]


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _metadata_repair(message: str) -> VerificationResult:
    return VerificationResult(
        outcome="repair",
        reason=(
            f"Planning verification metadata is malformed: {message}. "
            "Do not claim completion; repair the structured Planning requirements first."
        ),
        details={
            "code": "invalid_planning_verification_metadata",
            "metadata_error": message,
        },
    )


def _planning_requirements(
    initial_input: Mapping[str, object],
) -> _PlanningRequirements | VerificationResult | None:
    has_root = _ROOT_RUN_ID_FIELD in initial_input
    has_step = _STEP_ID_FIELD in initial_input
    if not has_root and not has_step:
        return None
    if not has_root or not has_step:
        missing = _ROOT_RUN_ID_FIELD if not has_root else _STEP_ID_FIELD
        return _metadata_repair(f"missing {missing}")

    root_run_id = initial_input.get(_ROOT_RUN_ID_FIELD)
    step_id = initial_input.get(_STEP_ID_FIELD)
    if not isinstance(root_run_id, str) or not root_run_id.strip():
        return _metadata_repair(f"{_ROOT_RUN_ID_FIELD} must be a non-empty string")
    if not isinstance(step_id, str) or not step_id.strip():
        return _metadata_repair(f"{_STEP_ID_FIELD} must be a non-empty string")

    raw_tool_names = initial_input.get(_REQUIRED_TOOLS_FIELD)
    if not isinstance(raw_tool_names, list) or any(
        not isinstance(value, str) or not value.strip() for value in raw_tool_names
    ):
        return _metadata_repair(f"{_REQUIRED_TOOLS_FIELD} must be a list of non-empty strings")

    raw_artifact_paths = initial_input.get(_REQUIRED_ARTIFACTS_FIELD)
    if not isinstance(raw_artifact_paths, list) or any(
        not isinstance(value, str) or not value.strip() for value in raw_artifact_paths
    ):
        return _metadata_repair(f"{_REQUIRED_ARTIFACTS_FIELD} must be a list of non-empty strings")

    return _PlanningRequirements(
        root_run_id=root_run_id,
        step_id=step_id,
        tool_names=_unique(raw_tool_names),
        artifact_paths=_unique(raw_artifact_paths),
    )


def _arguments(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _successful_tool_calls(state: RuntimeGraphState) -> tuple[_SuccessfulToolCall, ...]:
    raw_messages = state["lifecycle"].get("run_messages", [])
    if not isinstance(raw_messages, list):
        return ()

    proposed: dict[str, _SuccessfulToolCall] = {}
    succeeded_call_ids: set[str] = set()
    for message in raw_messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            raw_calls = message.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    continue
                call_id = raw_call.get("id")
                function = raw_call.get("function")
                if not isinstance(call_id, str) or not call_id or not isinstance(function, Mapping):
                    continue
                name = function.get("name")
                arguments = _arguments(function.get("arguments"))
                if not isinstance(name, str) or not name or arguments is None:
                    continue
                proposed[call_id] = _SuccessfulToolCall(
                    name=name,
                    arguments=arguments,
                )
        elif message.get("role") == "tool" and message.get("execution_status") == "succeeded":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                succeeded_call_ids.add(call_id)

    return tuple(call for call_id, call in proposed.items() if call_id in succeeded_call_ids)


class PlanningStepToolVerifier:
    """Require successful tool receipts before a Planning child may complete."""

    def __init__(self, base_verifier: RuntimeVerifier | None = None) -> None:
        self._base_verifier = base_verifier if base_verifier is not None else DeterministicRuntimeVerifier()

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult:
        base_result = await self._base_verifier.verify(state, context, candidate)
        if base_result.outcome != "pass":
            return base_result

        initial_input = state["snapshots"].initial_input
        requirements = _planning_requirements(initial_input)
        if requirements is None:
            return base_result
        if isinstance(requirements, VerificationResult):
            return requirements

        successful_calls = _successful_tool_calls(state)
        successful_names = {call.name for call in successful_calls}
        verified_tools = [name for name in requirements.tool_names if name in successful_names]
        artifact_refs = [
            path
            for path in requirements.artifact_paths
            if any(
                call.name == _ARTIFACT_WRITE_TOOL and call.arguments.get("path") == path for call in successful_calls
            )
        ]
        missing_tools = [name for name in requirements.tool_names if name not in successful_names]
        missing_artifacts = [path for path in requirements.artifact_paths if path not in artifact_refs]

        details = dict(base_result.details)
        details.update(
            {
                "planning_root_run_id": requirements.root_run_id,
                "planning_step_id": requirements.step_id,
                "verified_tools": verified_tools,
                "artifact_refs": artifact_refs,
            }
        )
        if missing_tools or missing_artifacts:
            details.update(
                {
                    "code": "planning_tool_evidence_missing",
                    "missing_tool_names": missing_tools,
                    "missing_artifact_paths": missing_artifacts,
                }
            )
            return VerificationResult(
                outcome="repair",
                reason=(
                    "Planning completion requires real tool calls with succeeded receipts. "
                    "Call every missing tool and write every missing artifact at its exact "
                    "required path; do not claim completion before that evidence exists."
                ),
                details=details,
            )

        details["code"] = "planning_tool_evidence_verified"
        return VerificationResult(outcome="pass", details=details)


__all__ = ["PlanningStepToolVerifier"]
