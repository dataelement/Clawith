"""Checkpoint-safe Tool Runtime contract tests."""

import pytest

from app.services.agent_runtime.tool_contracts import (
    AcceptedToolCall,
    StepToolContext,
    ToolContractError,
    ToolExecutionBinding,
    ToolWorksetEntry,
    parse_step_tool_context,
    workset_version,
)


def _entry() -> ToolWorksetEntry:
    return ToolWorksetEntry(
        tool_name="read_document",
        contract_version="builtin:read_document:v1",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        binding=ToolExecutionBinding(
            kind="builtin",
            handler_key="read_document",
        ),
        effect="read",
        retry_policy="safe",
        authorization_policy="runtime_default",
        deadline_policy="runtime_default",
        recovery_policy="safe_read",
    )


def test_step_tool_context_round_trips_three_distinct_identities() -> None:
    entry = _entry()
    accepted = AcceptedToolCall(
        call_instance_id="call-instance-1",
        provider_call_id="provider-call-7",
        entry=entry,
    )
    context = StepToolContext(
        assistant_message_id="assistant-1",
        model_step=3,
        workset_version=workset_version((entry,)),
        accepted_calls=(accepted,),
    )

    restored = parse_step_tool_context(context.to_json())

    assert restored == context
    assert restored.accepted_calls[0].call_instance_id == "call-instance-1"
    assert restored.accepted_calls[0].provider_call_id == "provider-call-7"
    assert "execution_id" not in restored.to_json()["accepted_calls"][0]


def test_legacy_checkpoint_may_omit_step_tool_context() -> None:
    assert parse_step_tool_context(None, allow_legacy_missing=True) is None

    with pytest.raises(ToolContractError, match="missing"):
        parse_step_tool_context(None, allow_legacy_missing=False)


def test_step_tool_context_rejects_unknown_versions_and_secret_material() -> None:
    payload = StepToolContext(
        assistant_message_id="assistant-1",
        model_step=1,
        workset_version=workset_version((_entry(),)),
        accepted_calls=(
            AcceptedToolCall(
                call_instance_id="call-1",
                provider_call_id=None,
                entry=_entry(),
            ),
        ),
    ).to_json()
    payload["version"] = 99

    with pytest.raises(ToolContractError, match="version"):
        parse_step_tool_context(payload)

    payload["version"] = 1
    accepted = payload["accepted_calls"][0]
    accepted["binding"]["target"] = {"api_key": "plain-secret"}

    with pytest.raises(ToolContractError, match="secret"):
        parse_step_tool_context(payload)


def test_workset_version_is_canonical_and_order_independent() -> None:
    first = _entry()
    second = ToolWorksetEntry(
        tool_name="write_file",
        contract_version="builtin:write_file:v1",
        parameters_schema={"type": "object", "properties": {}},
        binding=ToolExecutionBinding(kind="builtin", handler_key="write_file"),
        effect="write",
        retry_policy="conditional",
    )

    assert workset_version((first, second)) == workset_version((second, first))


def test_context_rejects_duplicate_call_instances_or_tool_mismatch() -> None:
    entry = _entry()
    call = AcceptedToolCall(
        call_instance_id="call-1",
        provider_call_id="provider-1",
        entry=entry,
    )

    with pytest.raises(ToolContractError, match="duplicate"):
        StepToolContext(
            assistant_message_id="assistant-1",
            model_step=1,
            workset_version=workset_version((entry,)),
            accepted_calls=(call, call),
        )

    payload = StepToolContext(
        assistant_message_id="assistant-1",
        model_step=1,
        workset_version=workset_version((entry,)),
        accepted_calls=(call,),
    ).to_json()
    payload["accepted_calls"][0]["tool_name"] = "write_file"

    with pytest.raises(ToolContractError, match="binding"):
        parse_step_tool_context(payload)
