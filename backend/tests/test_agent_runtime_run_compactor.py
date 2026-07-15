"""Run Compact trigger, batching, and Tool Exchange boundary tests."""

from __future__ import annotations

import json
import uuid

import pytest

from app.config import Settings
from app.models.llm import LLMModel
from app.services.agent_runtime.node_executor import RunCompactResult
from app.services.agent_runtime.run_compactor import (
    RunCompactInputs,
    RuntimeRunCompactorService,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.token_tracker import TokenUsage


class _UnusedSessionFactory:
    def __call__(self):
        raise AssertionError("injected input loader must avoid database access")


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_SESSION_RECENT_MESSAGES=20,
        **overrides,
    )


def _model(tenant_id: uuid.UUID, *, input_tokens: int | None = 100_000) -> LLMModel:
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
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
        ],
    }


def _tool_result(message_id: str, call_id: str) -> JsonObject:
    return {
        "id": message_id,
        "role": "tool",
        "tool_call_id": call_id,
        "content": "result",
    }


def _state(messages: list[JsonObject]) -> tuple[RuntimeGraphState, RuntimeContext, uuid.UUID]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="Complete the work",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(agent_id),
    )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={"version": 0, "summary": ""},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={"content": "start"},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "compact",
            "run_messages": messages,
            "pending_tool_calls": [],
        },
    }
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=object(),  # type: ignore[arg-type]
    )
    return state, context, tenant_id


def _step(summary_number: int) -> LLMCompletionStep:
    arguments = {
        "goal": "Complete the work",
        "progress": [f"batch-{summary_number}"],
        "completed_steps": [],
        "run_decisions": [],
        "blockers": [],
        "evidence_refs": [],
        "artifact_refs": [],
        "next_step": "continue",
    }
    return LLMCompletionStep(
        content="",
        tool_calls=(
            {
                "id": f"compact-{summary_number}",
                "type": "function",
                "function": {
                    "name": "commit_run_summary",
                    "arguments": json.dumps(arguments),
                },
            },
        ),
        reasoning_content=None,
        retry_instruction=None,
        usage=TokenUsage(total_tokens=10),
    )


def _loader(model: LLMModel, ledger: dict | None = None):
    async def load(_state: RuntimeGraphState) -> RunCompactInputs:
        return RunCompactInputs(model=model, ledger=ledger or {})

    return load


def _service(
    *,
    model: LLMModel,
    settings: Settings,
    completion,
    ledger: dict | None = None,
    phase_publisher=None,
) -> RuntimeRunCompactorService:
    return RuntimeRunCompactorService(
        session_factory=_UnusedSessionFactory(),  # type: ignore[arg-type]
        settings=settings,
        completion=completion,
        input_loader=_loader(model, ledger),
        phase_publisher=phase_publisher,
    )


@pytest.mark.asyncio
async def test_message_threshold_compacts_only_prefix_before_recent_twenty() -> None:
    messages = [_normal(f"message-{index}") for index in range(25)]
    state, context, tenant_id = _state(messages)
    calls = 0
    phases: list[str] = []

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _step(calls)

    async def publish_phase(_state, status: str) -> None:
        phases.append(status)

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=21),
        completion=complete,
        phase_publisher=publish_phase,
    ).compact_if_needed(state, context, forced=False)

    assert result.compacted is True
    assert result.covered_through_run_message_id == "message-4"
    assert result.run_messages == tuple(messages[5:])
    assert result.run_summary is not None
    assert result.run_summary["progress"] == ["batch-1"]
    assert calls == 1
    assert phases == ["compacting", "working"]


@pytest.mark.asyncio
async def test_retryable_compact_model_error_retries_once() -> None:
    messages = [_normal(f"message-{index}") for index in range(25)]
    state, context, tenant_id = _state(messages)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary compact provider failure")
        return _step(calls)

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=21),
        completion=complete,
    ).compact_if_needed(state, context, forced=False)

    assert result.compacted is True
    assert result.covered_through_run_message_id == "message-4"
    assert calls == 2


@pytest.mark.asyncio
async def test_recent_window_expands_to_keep_complete_tool_exchange() -> None:
    old = _normal("old")
    exchange = [
        _assistant("assistant-tools", "call-1"),
        _tool_result("result-1", "call-1"),
    ]
    recent = [_normal(f"recent-{index}") for index in range(19)]
    state, context, tenant_id = _state([old, *exchange, *recent])

    async def complete(*_args, **_kwargs):
        return _step(1)

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(),
        completion=complete,
        ledger={"call-1": {"status": "succeeded"}},
    ).compact_if_needed(state, context, forced=True)

    assert result.compacted is True
    assert result.covered_through_run_message_id == "old"
    assert result.run_messages == tuple([*exchange, *recent])


@pytest.mark.asyncio
async def test_started_tool_exchange_is_never_crossed_by_compact_watermark() -> None:
    old = _normal("old-safe")
    pending = _assistant("assistant-pending", "call-pending")
    recent = [_normal(f"recent-{index}") for index in range(20)]
    state, context, tenant_id = _state([old, pending, *recent])

    async def complete(*_args, **_kwargs):
        return _step(1)

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(),
        completion=complete,
        ledger={"call-pending": {"status": "started"}},
    ).compact_if_needed(state, context, forced=True)

    assert result.compacted is True
    assert result.covered_through_run_message_id == "old-safe"
    assert result.run_messages == tuple([pending, *recent])


@pytest.mark.asyncio
async def test_no_safe_prefix_before_pending_exchange_keeps_all_exact_messages() -> None:
    pending = _assistant("assistant-pending", "call-pending")
    recent = [_normal(f"recent-{index}") for index in range(20)]
    state, context, tenant_id = _state([pending, *recent])

    async def complete(*_args, **_kwargs):
        raise AssertionError("unsafe history must not reach the compact model")

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(),
        completion=complete,
        ledger={"call-pending": {"status": "unknown", "may_have_side_effect": True}},
    ).compact_if_needed(state, context, forced=True)

    assert result == RunCompactResult(
        error={
            "code": "run_compact_boundary_unavailable",
            "message": "No complete safe prefix exists before the recent Run window",
        }
    )


@pytest.mark.asyncio
async def test_compact_batches_never_split_large_message_blocks() -> None:
    messages = [
        _normal("old-a", "a" * 6_000),
        _normal("old-b", "b" * 6_000),
        *[_normal(f"recent-{index}") for index in range(20)],
    ]
    state, context, tenant_id = _state(messages)
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step(len(payloads))

    result = await _service(
        model=_model(tenant_id, input_tokens=5_000),
        settings=_settings(),
        completion=complete,
    ).compact_if_needed(state, context, forced=True)

    assert result.compacted is True
    assert len(payloads) == 2
    assert [len(payload["covered_messages"]) for payload in payloads] == [1, 1]
    assert result.covered_through_run_message_id == "old-b"


@pytest.mark.asyncio
async def test_short_run_below_all_thresholds_skips_model_call() -> None:
    state, context, tenant_id = _state([_normal("only-message")])

    async def complete(*_args, **_kwargs):
        raise AssertionError("short Run must not be compacted")

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(),
        completion=complete,
    ).compact_if_needed(state, context, forced=False)

    assert result == RunCompactResult()


@pytest.mark.asyncio
async def test_eighty_five_percent_token_threshold_compacts_even_under_twenty_messages() -> None:
    messages = [
        _normal(f"large-{index}", str(index) * 9_000)
        for index in range(5)
    ]
    state, context, tenant_id = _state(messages)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _step(calls)

    result = await _service(
        model=_model(tenant_id, input_tokens=15_000),
        settings=_settings(),
        completion=complete,
    ).compact_if_needed(state, context, forced=False)

    assert result.compacted is True
    assert result.run_messages is not None
    assert len(result.run_messages) < len(messages)
    assert result.covered_through_run_message_id is not None
    assert calls >= 1


@pytest.mark.asyncio
async def test_unknown_model_input_metadata_does_not_trigger_token_compaction() -> None:
    state, context, tenant_id = _state(
        [_normal("oversized", "x" * 500_000)]
    )

    async def complete(*_args, **_kwargs):
        raise AssertionError("unknown metadata must not trigger local token compaction")

    result = await _service(
        model=_model(tenant_id, input_tokens=None),
        settings=_settings(),
        completion=complete,
    ).compact_if_needed(state, context, forced=False)

    assert result == RunCompactResult()


@pytest.mark.asyncio
async def test_unknown_model_input_metadata_keeps_count_trigger_without_token_batching() -> None:
    messages = [
        _normal("old-a", "a" * 100_000),
        _normal("old-b", "b" * 100_000),
        *[_normal(f"recent-{index}") for index in range(20)],
    ]
    state, context, tenant_id = _state(messages)
    payloads: list[dict] = []

    async def complete(_model, prompt, **_kwargs):
        payloads.append(json.loads(prompt[1].content))
        return _step(len(payloads))

    result = await _service(
        model=_model(tenant_id, input_tokens=None),
        settings=_settings(AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=21),
        completion=complete,
    ).compact_if_needed(state, context, forced=False)

    assert result.compacted is True
    assert result.covered_through_run_message_id == "old-b"
    assert result.run_messages == tuple(messages[2:])
    assert len(payloads) == 1
    assert len(payloads[0]["covered_messages"]) == 2


@pytest.mark.asyncio
async def test_invalid_compact_output_keeps_previous_checkpoint_history() -> None:
    messages = [_normal(f"message-{index}") for index in range(21)]
    state, context, tenant_id = _state(messages)
    phases: list[str] = []

    async def complete(*_args, **_kwargs):
        return LLMCompletionStep(
            content="free text",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=1),
        )

    async def publish_phase(_state, status: str) -> None:
        phases.append(status)

    result = await _service(
        model=_model(tenant_id),
        settings=_settings(),
        completion=complete,
        phase_publisher=publish_phase,
    ).compact_if_needed(state, context, forced=True)

    assert result.compacted is False
    assert result.error is not None
    assert result.error["code"] == "invalid_run_compact_output"
    assert phases == ["compacting", "working"]
