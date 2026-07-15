"""Receipt-backed completion tests for delegated group Planning steps."""

from __future__ import annotations

import json
from typing import cast
import uuid

import pytest

from app.services.agent_runtime.group_planning_verifier import PlanningStepToolVerifier
from app.services.agent_runtime.node_executor import VerificationResult
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)


def _state(
    *,
    initial_input: dict | None = None,
    run_messages: list[dict] | None = None,
) -> RuntimeGraphState:
    return {
        "registry": RunRegistrySnapshot(
            tenant_id="tenant-1",
            run_id=str(uuid.uuid4()),
            goal="Complete one Planning step",
            run_kind="background",
            source_type="group_planning",
            model_id="model-1",
            graph_name="runtime_test",
            graph_version="v1",
            agent_id="agent-1",
            session_id="session-1",
        ),
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input=initial_input or {},
        ),
        "lifecycle": {
            "status": "verifying",
            "next_route": "verify",
            "run_messages": run_messages or [],
            "pending_tool_calls": [],
        },
    }


def _planning_input(
    *,
    tools: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> dict:
    return {
        "planning_root_run_id": "root-run-1",
        "planning_step_id": "step-1",
        "planning_required_tool_names": tools or [],
        "planning_required_artifact_paths": artifacts or [],
    }


def _assistant_tool_call(
    call_id: str,
    name: str,
    arguments: dict | str | None = None,
) -> dict:
    encoded_arguments = (
        arguments if isinstance(arguments, str) else json.dumps(arguments if arguments is not None else {})
    )
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": encoded_arguments,
                },
            }
        ],
    }


def _tool_receipt(call_id: str, *, status: str = "succeeded") -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "execution_status": status,
        "content": "receipt",
    }


async def _verify(
    state: RuntimeGraphState,
    verifier: PlanningStepToolVerifier | None = None,
) -> VerificationResult:
    return await (verifier or PlanningStepToolVerifier()).verify(
        state,
        cast(RuntimeContext, object()),
        "Completed",
    )


class _BaseVerifier:
    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult:
        del state, context
        self.calls.append(candidate)
        return self.result


@pytest.mark.asyncio
async def test_missing_required_tool_repairs_instead_of_accepting_claim() -> None:
    state = _state(
        initial_input=_planning_input(tools=["group_list_workspace"]),
        run_messages=[
            _assistant_tool_call("call-other", "group_list_members"),
            _tool_receipt("call-other"),
        ],
    )

    result = await _verify(state)

    assert result.outcome == "repair"
    assert result.details["missing_tool_names"] == ["group_list_workspace"]
    assert result.details["verified_tools"] == []
    assert "real tool calls" in (result.reason or "")
    assert "do not claim completion" in (result.reason or "")


@pytest.mark.asyncio
async def test_failed_tool_receipt_does_not_satisfy_required_tool() -> None:
    state = _state(
        initial_input=_planning_input(tools=["group_list_workspace"]),
        run_messages=[
            _assistant_tool_call("call-list", "group_list_workspace"),
            _tool_receipt("call-list", status="failed"),
        ],
    )

    result = await _verify(state)

    assert result.outcome == "repair"
    assert result.details["missing_tool_names"] == ["group_list_workspace"]


@pytest.mark.asyncio
async def test_artifact_requires_successful_write_to_exact_path() -> None:
    required_path = "deliverables/plan.md"
    wrong_path_state = _state(
        initial_input=_planning_input(artifacts=[required_path]),
        run_messages=[
            _assistant_tool_call(
                "call-write",
                "group_write_workspace_file",
                {"path": "deliverables/Plan.md", "content": "draft"},
            ),
            _tool_receipt("call-write"),
        ],
    )

    wrong_path_result = await _verify(wrong_path_state)

    assert wrong_path_result.outcome == "repair"
    assert wrong_path_result.details["missing_artifact_paths"] == [required_path]

    exact_path_state = _state(
        initial_input=_planning_input(artifacts=[required_path]),
        run_messages=[
            _assistant_tool_call(
                "call-write",
                "group_write_workspace_file",
                {"path": required_path, "content": "draft"},
            ),
            _tool_receipt("call-write"),
        ],
    )

    exact_path_result = await _verify(exact_path_state)

    assert exact_path_result.outcome == "pass"
    assert exact_path_result.details["artifact_refs"] == [required_path]


@pytest.mark.asyncio
async def test_all_required_tools_and_artifacts_pass_with_verified_details() -> None:
    artifact_path = "deliverables/plan.md"
    state = _state(
        initial_input=_planning_input(
            tools=["group_list_workspace", "group_write_workspace_file"],
            artifacts=[artifact_path],
        ),
        run_messages=[
            _assistant_tool_call("call-list", "group_list_workspace"),
            _tool_receipt("call-list"),
            _assistant_tool_call(
                "call-write",
                "group_write_workspace_file",
                {"path": artifact_path, "content": "verified"},
            ),
            _tool_receipt("call-write"),
        ],
    )

    result = await _verify(state)

    assert result.outcome == "pass"
    assert result.details == {
        "code": "planning_tool_evidence_verified",
        "planning_root_run_id": "root-run-1",
        "planning_step_id": "step-1",
        "verified_tools": ["group_list_workspace", "group_write_workspace_file"],
        "artifact_refs": [artifact_path],
    }


@pytest.mark.asyncio
async def test_non_planning_run_preserves_base_verifier_result() -> None:
    base_result = VerificationResult(
        outcome="pass",
        reason="base accepted",
        details={"code": "custom_base"},
    )
    base = _BaseVerifier(base_result)

    result = await _verify(
        _state(initial_input={"message_id": "message-1"}),
        PlanningStepToolVerifier(base),
    )

    assert result is base_result
    assert base.calls == ["Completed"]


@pytest.mark.asyncio
async def test_non_pass_base_result_short_circuits_planning_checks() -> None:
    base_result = VerificationResult(
        outcome="repair",
        reason="pending tools remain",
        details={"code": "pending_tools"},
    )
    base = _BaseVerifier(base_result)
    malformed_planning_input = _planning_input(tools=["required_tool"])
    malformed_planning_input["planning_required_tool_names"] = "required_tool"

    result = await _verify(
        _state(initial_input=malformed_planning_input),
        PlanningStepToolVerifier(base),
    )

    assert result is base_result
    assert base.calls == ["Completed"]


@pytest.mark.asyncio
async def test_malformed_planning_metadata_repairs_conservatively() -> None:
    malformed = _planning_input(tools=["group_list_workspace"])
    malformed["planning_required_artifact_paths"] = "deliverables/plan.md"

    result = await _verify(_state(initial_input=malformed))

    assert result.outcome == "repair"
    assert result.details["code"] == "invalid_planning_verification_metadata"
    assert "planning_required_artifact_paths" in result.details["metadata_error"]
    assert "Do not claim completion" in (result.reason or "")
