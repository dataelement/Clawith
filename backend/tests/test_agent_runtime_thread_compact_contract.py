from __future__ import annotations

from typing import get_type_hints
import uuid

import pytest

from app.services.agent_runtime.graph import COMPACT_RETRY_POLICY
from app.services.agent_runtime.command_worker import RuntimeRunRecord
from app.services.agent_runtime.node_executor import (
    DeterministicRuntimeNodeExecutor,
    ModelStepResult,
    ToolStepResult,
)
from app.services.agent_runtime.run_compactor import (
    _SUMMARY_FIELDS,
    RunCompactorError,
    TransientRunCompactorError,
    compact_context_budgets,
    reaches_compact_high_watermark,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.agent_runtime.tool_exchange import (
    build_message_blocks,
    select_recent_blocks,
)


def _state(*, next_route: str = "compact") -> RuntimeGraphState:
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            goal="Keep the exact current request",
            run_kind="foreground",
            source_type="chat",
            model_id=str(uuid.uuid4()),
            graph_name="agent_runtime",
            graph_version="v1",
            agent_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
        ),
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={"input_content": "Keep the exact current request"},
        ),
        "messages": [],
        "lifecycle": {
            "status": "running",
            "next_route": next_route,  # type: ignore[typeddict-item]
            "model_step_count": 0,
            "pending_tool_calls": [],
        },
    }


class _NoCancel:
    async def get_cancel(self, state, context):
        del state, context
        return None


class _WaitModel:
    async def complete_once(self, state, context):
        del state, context
        return ModelStepResult(
            intent="wait",
            waiting_request={
                "waiting_type": "user",
                "correlation_id": "reply-1",
                "reason": "Need exact input",
            },
        )


class _NoTools:
    async def execute_pending(self, state, context, tool_calls):
        del state, context, tool_calls
        return ToolStepResult()


class _ExplodingCompactor:
    async def compact_if_needed(self, state, context):
        del state, context
        raise TimeoutError("compact provider timed out")


def _context(state: RuntimeGraphState) -> RuntimeContext:
    registry = state["registry"]
    return RuntimeContext(
        tenant_id=registry.tenant_id,
        run_id=registry.run_id,
        command_id=str(uuid.uuid4()),
        executor=None,  # type: ignore[arg-type]
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


def test_state_uses_langgraph_messages_channel_not_run_message_mirror() -> None:
    hints = get_type_hints(RuntimeGraphState, include_extras=True)

    assert "messages" in hints
    assert "run_messages" not in RuntimeGraphState.__annotations__
    assert "run_summary" not in RuntimeGraphState.__annotations__


def test_product_run_record_flattens_runtime_context_without_registry_wrapper() -> None:
    fields = RuntimeRunRecord.__dataclass_fields__

    assert "registry" not in fields
    assert {
        "goal",
        "run_kind",
        "source_type",
        "model_id",
        "graph_name",
        "graph_version",
        "agent_id",
        "session_id",
        "system_role",
        "parent_run_id",
        "root_run_id",
        "model_turn_limit",
    } <= fields.keys()


def test_compact_summary_has_exactly_the_five_frozen_sections() -> None:
    assert _SUMMARY_FIELDS == frozenset(
        {
            "task_goal_and_constraints",
            "completed_work_and_results",
            "key_decisions_and_evidence",
            "unfinished_or_blocked",
            "next_actions",
        }
    )


@pytest.mark.parametrize(
    ("effective_budget", "expected_summary", "expected_recent"),
    [
        (10_000, 2_500, 2_500),
        (32_000, 4_096, 8_000),
        (100, 25, 25),
    ],
)
def test_compact_uses_frozen_25_percent_component_budgets(
    effective_budget: int,
    expected_summary: int,
    expected_recent: int,
) -> None:
    budgets = compact_context_budgets(effective_budget)

    assert budgets.summary_tokens == expected_summary
    assert budgets.recent_tokens == expected_recent
    assert budgets.summary_tokens + budgets.recent_tokens <= effective_budget // 2


def test_compact_high_watermark_is_exactly_80_percent() -> None:
    assert reaches_compact_high_watermark(799, effective_input_budget=1_000) is False
    assert reaches_compact_high_watermark(800, effective_input_budget=1_000) is True


def test_compact_retry_policy_is_three_attempts_and_transient_only() -> None:
    assert COMPACT_RETRY_POLICY.max_attempts == 3
    assert callable(COMPACT_RETRY_POLICY.retry_on)
    assert COMPACT_RETRY_POLICY.retry_on(
        TransientRunCompactorError("provider_timeout", "retry")
    )
    assert not COMPACT_RETRY_POLICY.retry_on(
        RunCompactorError("invalid_summary", "do not retry")
    )


def test_recent_suffix_has_no_message_count_cutoff() -> None:
    messages = [
        {"id": f"m-{index}", "role": "user", "content": f"message {index}"}
        for index in range(25)
    ]
    blocks = build_message_blocks(messages)

    selected = select_recent_blocks(
        blocks,
        target_messages=None,
        token_budget=10_000,
        token_counter=lambda values: len(values),
    )

    assert [message["id"] for message in selected.messages] == [
        f"m-{index}" for index in range(25)
    ]


@pytest.mark.asyncio
async def test_wait_does_not_trigger_compact_without_a_business_model_call() -> None:
    state = _state(next_route="model")
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=_NoCancel(),
        model_service=_WaitModel(),
        tool_service=_NoTools(),
    )

    update = await executor.execute("model", state, _context(state))

    assert update["lifecycle"]["status"] == "waiting_user"
    assert update["lifecycle"]["next_route"] == "wait"


@pytest.mark.asyncio
async def test_compact_exception_is_not_swallowed_or_converted_to_state() -> None:
    state = _state()
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=_NoCancel(),
        model_service=_WaitModel(),
        tool_service=_NoTools(),
        run_compactor=_ExplodingCompactor(),
    )

    with pytest.raises(TimeoutError, match="timed out"):
        await executor.execute("compact", state, _context(state))

    assert "thread_summary" not in state
    assert "summary_covered_through_message_id" not in state
