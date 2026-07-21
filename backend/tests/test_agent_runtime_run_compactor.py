"""Frozen D-016 Thread Running Summary and semantic-boundary tests."""

from __future__ import annotations

import json
import uuid

import pytest

from app.config import Settings
from app.models.llm import LLMModel
from app.services.agent_runtime.run_compactor import (
    RunCompactInputs,
    RunCompactorError,
    RuntimeRunCompactorService,
    TransientRunCompactorError,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.llm.finish import FINISH_PROTOCOL_REMINDER
from app.services.token_tracker import TokenUsage


def _settings() -> Settings:
    return Settings(_env_file=None)


def _model(tenant_id: uuid.UUID, *, input_tokens: int = 100_000) -> LLMModel:
    return LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="compact-model",
        label="Compact",
        api_key_encrypted="encrypted",
        enabled=True,
        max_input_tokens=input_tokens,
        max_output_tokens=256,
    )


def _normal(message_id: str, content: str | None = None) -> JsonObject:
    return {
        "id": message_id,
        "role": "user",
        "content": content or message_id,
    }


def _assistant(message_id: str, call_id: str) -> JsonObject:
    return {
        "id": message_id,
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
        ],
    }


def _tool_result(
    message_id: str,
    call_id: str,
    *,
    content: str = "result",
) -> JsonObject:
    return {
        "id": message_id,
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
    }


def _state(messages: list[JsonObject]) -> tuple[RuntimeGraphState, RuntimeContext, uuid.UUID]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    current = next(
        (
            message
            for message in reversed(messages)
            if message.get("runtime_input") == "current"
        ),
        messages[-1],
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="Complete the work",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(uuid.uuid4()),
    )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "message_id": current["id"],
                "input_content": current["content"],
            },
        ),
        "messages": messages,  # type: ignore[typeddict-item]
        "lifecycle": {
            "status": "running",
            "next_route": "compact",
            "pending_tool_calls": [],
        },
    }
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=object(),  # type: ignore[arg-type]
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
        model_turn_limit=50,
    )
    return state, context, tenant_id


def _step(**overrides: str) -> LLMCompletionStep:
    arguments = {
        "task_goal_and_constraints": "Complete the work accurately",
        "completed_work_and_results": "Reviewed earlier context",
        "key_decisions_and_evidence": "Use the durable receipt",
        "unfinished_or_blocked": "No blockers",
        "next_actions": "Answer the exact current request",
        **overrides,
    }
    return LLMCompletionStep(
        content="",
        tool_calls=(
            {
                "id": "compact-1",
                "type": "function",
                "function": {
                    "name": "commit_thread_summary",
                    "arguments": json.dumps(arguments),
                },
            },
        ),
        reasoning_content=None,
        retry_instruction=None,
        usage=TokenUsage(total_tokens=10),
    )


def _service(
    *,
    model: LLMModel,
    completion,
    effective_budget: int,
    current_tokens: int,
    ledger: dict | None = None,
) -> RuntimeRunCompactorService:
    async def load(
        _state: RuntimeGraphState,
        _context: RuntimeContext,
    ) -> RunCompactInputs:
        return RunCompactInputs(
            model=model,
            ledger=ledger or {},
            effective_input_budget=effective_budget,
            current_input_tokens=current_tokens,
        )

    return RuntimeRunCompactorService(
        settings=_settings(),
        completion=completion,
        input_loader=load,
    )


@pytest.mark.asyncio
async def test_below_eighty_percent_skips_compact() -> None:
    messages = [_normal("old"), _normal("current")]
    state, context, tenant_id = _state(messages)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("sub-80% request must not call the compact model")

    result = await _service(
        model=_model(tenant_id),
        completion=forbidden,
        effective_budget=1_000,
        current_tokens=799,
    ).compact_if_needed(state, context)

    assert result.compacted is False


@pytest.mark.asyncio
async def test_missing_complete_business_request_budget_fails_closed() -> None:
    state, context, tenant_id = _state(
        [_normal("old", "old " * 300), _normal("current")]
    )

    async def load(
        _state: RuntimeGraphState,
        _context: RuntimeContext,
    ) -> RunCompactInputs:
        return RunCompactInputs(model=_model(tenant_id), ledger={})

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("missing request budget must fail before model use")

    service = RuntimeRunCompactorService(
        settings=_settings(),
        completion=forbidden,
        input_loader=load,
    )

    with pytest.raises(RunCompactorError) as raised:
        await service.compact_if_needed(state, context)

    assert raised.value.code == "missing_request_budget"


@pytest.mark.asyncio
async def test_at_eighty_percent_compacts_prefix_and_keeps_current_input_exact() -> None:
    messages = [
        *[_normal(f"old-{index}", "old history " * 12) for index in range(8)],
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
    ]
    state, context, tenant_id = _state(messages)
    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=800,
    ).compact_if_needed(state, context)

    assert result.compacted is True
    assert result.thread_summary is not None
    assert set(result.thread_summary) == {
        "task_goal_and_constraints",
        "completed_work_and_results",
        "key_decisions_and_evidence",
        "unfinished_or_blocked",
        "next_actions",
    }
    assert result.recent_messages is not None
    assert result.recent_messages[-1]["content"] == "EXACT CURRENT INPUT"
    assert result.recent_messages[-1]["runtime_input"] == "current"
    assert result.covered_through_message_id != "current"


@pytest.mark.asyncio
async def test_long_single_run_compacts_safe_work_after_exact_current_input() -> None:
    messages = [
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
        _normal("completed-work", "completed work " * 300),
        _normal("recent", "recent result"),
    ]
    state, context, tenant_id = _state(messages)
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    assert result.compacted is True
    assert result.covered_through_message_id == "completed-work"
    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == [
        "current",
        "recent",
    ]
    assert result.recent_messages[0]["content"] == "EXACT CURRENT INPUT"
    assert payloads[0]["authoritative_exact_inputs"][0]["content"] == (
        "EXACT CURRENT INPUT"
    )


@pytest.mark.asyncio
async def test_prior_run_input_marker_does_not_pin_current_run_compact() -> None:
    messages = [
        {
            **_normal("prior-run-input", "prior input " * 300),
            "runtime_input": "current",
            "runtime_run_id": str(uuid.uuid4()),
        },
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
    ]
    state, context, tenant_id = _state(messages)

    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    assert result.covered_through_message_id == "prior-run-input"
    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == ["current"]


@pytest.mark.asyncio
async def test_prior_run_plain_candidates_and_repairs_never_enter_compact_summary() -> None:
    prior_run_id = str(uuid.uuid4())
    messages = [
        {
            **_normal("prior-input", "prior input " * 300),
            "runtime_input": "current",
            "runtime_run_id": prior_run_id,
        },
        {
            "id": "prior-draft",
            "role": "assistant",
            "content": "PRIVATE REPLACED DRAFT",
            "runtime_run_id": prior_run_id,
        },
        {
            "id": "prior-repair",
            "role": "user",
            "content": FINISH_PROTOCOL_REMINDER,
            "runtime_intent": "repair",
            "runtime_run_id": prior_run_id,
        },
        {
            "id": "prior-finish-candidate",
            "role": "assistant",
            "content": "THREAD TERMINAL CANDIDATE",
            "runtime_intent": "finish",
            "runtime_run_id": prior_run_id,
        },
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
    ]
    state, context, tenant_id = _state(messages)
    state["messages"][-1]["runtime_run_id"] = context.run_id  # type: ignore[index]
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    serialized_payload = json.dumps(payloads, ensure_ascii=False)
    assert "PRIVATE REPLACED DRAFT" not in serialized_payload
    assert "THREAD TERMINAL CANDIDATE" not in serialized_payload
    assert FINISH_PROTOCOL_REMINDER not in serialized_payload
    assert result.recent_messages is not None
    recent_contents = [str(message.get("content", "")) for message in result.recent_messages]
    assert "PRIVATE REPLACED DRAFT" not in recent_contents
    assert "THREAD TERMINAL CANDIDATE" not in recent_contents
    assert FINISH_PROTOCOL_REMINDER not in recent_contents


@pytest.mark.asyncio
async def test_current_run_repair_state_stays_raw_but_out_of_compact_prompt() -> None:
    messages = [
        _normal("old-safe", "old completed history " * 300),
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
        {
            "id": "current-draft",
            "role": "assistant",
            "content": "CURRENT PRIVATE DRAFT",
            "runtime_intent": "repair_draft",
        },
        {
            "id": "current-repair",
            "role": "user",
            "content": FINISH_PROTOCOL_REMINDER,
            "runtime_intent": "repair",
        },
    ]
    state, context, tenant_id = _state(messages)
    for message in state["messages"][1:]:  # type: ignore[index]
        message["runtime_run_id"] = context.run_id
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == [
        "current",
        "current-draft",
        "current-repair",
    ]
    exact_inputs = payloads[0]["authoritative_exact_inputs"]
    assert [message["id"] for message in exact_inputs] == ["current"]
    serialized_payload = json.dumps(payloads, ensure_ascii=False)
    assert "CURRENT PRIVATE DRAFT" not in serialized_payload
    assert FINISH_PROTOCOL_REMINDER not in serialized_payload


@pytest.mark.asyncio
async def test_current_run_resume_input_remains_exact_across_later_compact() -> None:
    messages = [
        {
            **_normal("current", "EXACT CURRENT INPUT"),
            "runtime_input": "current",
        },
        _normal("before-resume", "completed before resume " * 160),
        {
            **_normal("resume", "EXACT RESUME INPUT"),
            "runtime_input": "resume",
        },
        _normal("after-resume", "completed after resume " * 160),
        _normal("recent", "recent result"),
    ]
    state, context, tenant_id = _state(messages)
    state["messages"][2]["runtime_run_id"] = context.run_id  # type: ignore[index]

    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    assert result.covered_through_message_id == "after-resume"
    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == [
        "current",
        "resume",
        "recent",
    ]
    assert result.recent_messages[1]["content"] == "EXACT RESUME INPUT"


@pytest.mark.asyncio
async def test_generated_current_message_id_is_protected_by_run_identity() -> None:
    messages = [
        {
            **_normal("generated-current", "EXACT GENERATED INPUT"),
            "runtime_input": "current",
        },
        _normal("completed-work", "completed work " * 300),
        _normal("recent", "recent result"),
    ]
    state, context, tenant_id = _state(messages)
    state["messages"][0]["runtime_run_id"] = context.run_id  # type: ignore[index]
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"input_content": "EXACT GENERATED INPUT"},
    )

    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
    ).compact_if_needed(state, context)

    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == [
        "generated-current",
        "recent",
    ]


@pytest.mark.asyncio
async def test_started_exchange_is_retained_and_never_crossed() -> None:
    messages = [
        _normal("old-safe", "old " * 300),
        _assistant("assistant-pending", "call-pending"),
        {**_normal("current", "exact"), "runtime_input": "current"},
    ]
    state, context, tenant_id = _state(messages)

    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
        ledger={"call-pending": {"status": "started"}},
    ).compact_if_needed(state, context)

    assert result.covered_through_message_id == "old-safe"
    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == [
        "assistant-pending",
        "current",
    ]


@pytest.mark.asyncio
async def test_cancelled_not_started_exchange_can_enter_summary() -> None:
    messages = [
        _normal("old-safe", "old " * 300),
        _assistant("assistant-cancelled", "call-cancelled"),
        {**_normal("current", "exact"), "runtime_input": "current"},
    ]
    state, context, tenant_id = _state(messages)

    async def complete(*_args, **_kwargs):
        return _step()

    result = await _service(
        model=_model(tenant_id),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
        ledger={
            "call-cancelled": {
                "status": "not_started",
                "tool_name": "lookup",
                "cancelled_before_execution": True,
                "may_have_side_effect": False,
            }
        },
    ).compact_if_needed(state, context)

    assert result.covered_through_message_id == "assistant-cancelled"
    assert result.recent_messages is not None
    assert [message["id"] for message in result.recent_messages] == ["current"]


@pytest.mark.asyncio
async def test_oversized_settled_exchange_enters_summary_as_facts_and_refs() -> None:
    messages = [
        _assistant("assistant-tools", "call-1"),
        _tool_result("result-1", "call-1", content="x" * 30_000),
        {**_normal("current", "exact"), "runtime_input": "current"},
    ]
    state, context, tenant_id = _state(messages)
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step()

    result = await _service(
        model=_model(tenant_id, input_tokens=5_000),
        completion=complete,
        effective_budget=1_000,
        current_tokens=900,
        ledger={
            "call-1": {
                "status": "succeeded",
                "tool_name": "lookup",
                "result_summary": "found the answer",
                "result_ref": "result://call-1",
                "request_ref": "request://call-1",
            }
        },
    ).compact_if_needed(state, context)

    assert result.compacted is True
    serialized = json.dumps(payloads, ensure_ascii=False)
    assert "historical_tool_exchange" in serialized
    assert "result://call-1" in serialized
    assert "request://call-1" in serialized
    assert "x" * 1_000 not in serialized


@pytest.mark.asyncio
async def test_transient_provider_failure_is_typed_for_langgraph_retry() -> None:
    state, context, tenant_id = _state(
        [_normal("old", "old " * 300), _normal("current")]
    )

    async def complete(*_args, **_kwargs):
        raise TimeoutError("provider timeout")

    with pytest.raises(TransientRunCompactorError) as raised:
        await _service(
            model=_model(tenant_id),
            completion=complete,
            effective_budget=1_000,
            current_tokens=900,
        ).compact_if_needed(state, context)

    assert raised.value.is_transient_compact_error is True


@pytest.mark.asyncio
async def test_invalid_summary_is_deterministic_and_never_committed() -> None:
    state, context, tenant_id = _state(
        [_normal("old", "old " * 300), _normal("current")]
    )

    async def complete(*_args, **_kwargs):
        return LLMCompletionStep(
            content="free text",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=1),
        )

    with pytest.raises(RunCompactorError) as raised:
        await _service(
            model=_model(tenant_id),
            completion=complete,
            effective_budget=1_000,
            current_tokens=900,
        ).compact_if_needed(state, context)

    assert raised.value.code == "invalid_thread_compact_output"
    assert "thread_summary" not in state
    assert "summary_covered_through_message_id" not in state


@pytest.mark.asyncio
async def test_summary_over_4096_tokens_is_rejected() -> None:
    state, context, tenant_id = _state(
        [_normal("old", "old " * 15_000), _normal("current")]
    )

    async def complete(*_args, **_kwargs):
        return _step(completed_work_and_results="x" * 20_000)

    with pytest.raises(RunCompactorError) as raised:
        await _service(
            model=_model(tenant_id, input_tokens=100_000),
            completion=complete,
            effective_budget=100_000,
            current_tokens=80_000,
        ).compact_if_needed(state, context)

    assert raised.value.code == "thread_summary_exceeds_budget"
