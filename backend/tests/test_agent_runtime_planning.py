"""Checkpoint-owned Planning Graph contract and transition tests."""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
import json
from typing import cast
import uuid

import pytest

from app.models.llm import LLMModel
from app.services.agent_runtime.planning import (
    PlanningContractError,
    PlanningModelResult,
    PlanningModelService,
    PlanningRuntimeNodeExecutor,
    ready_plan_steps,
    validate_planning_output,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeExecutor,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.token_tracker import TokenUsage


def _candidate(agent_id: uuid.UUID, name: str) -> JsonObject:
    return {
        "agent_id": str(agent_id),
        "participant_id": str(uuid.uuid4()),
        "name": name,
        "role_description": f"Role for {name}",
    }


def _state(
    agent_ids: tuple[uuid.UUID, ...],
    *,
    model_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> RuntimeGraphState:
    resolved_run_id = run_id or uuid.uuid4()
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(uuid.uuid4()),
            run_id=str(resolved_run_id),
            goal="Research the topic, then write the answer",
            run_kind="orchestration",
            source_type="chat",
            model_id=str(model_id or uuid.uuid4()),
            graph_name="runtime_group_planning",
            graph_version="v1",
            session_id=str(uuid.uuid4()),
            system_role="group_planning",
        ),
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "candidate_agents": [
                    _candidate(agent_id, f"Agent {index}")
                    for index, agent_id in enumerate(agent_ids, start=1)
                ]
            },
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "model",
            "run_messages": [],
            "pending_tool_calls": [],
        },
    }


def _plan(
    first: uuid.UUID,
    second: uuid.UUID,
    *,
    strategy: str = "sequential",
) -> dict:
    dependencies = [] if strategy == "parallel" else ["research"]
    return {
        "version": 1,
        "goal": "Produce one grounded answer",
        "execution_strategy": strategy,
        "steps": [
            {
                "step_id": "research",
                "agent_id": str(first),
                "instruction": "Research the evidence",
                "depends_on_step_ids": [],
            },
            {
                "step_id": "write",
                "agent_id": str(second),
                "instruction": "Write the final answer",
                "depends_on_step_ids": dependencies,
            },
        ],
    }


def _context(state: RuntimeGraphState) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=state["registry"].tenant_id,
        run_id=state["registry"].run_id,
        command_id=str(uuid.uuid4()),
        executor=cast(RuntimeNodeExecutor, object()),
    )


class _CancelSource:
    async def get_cancel(self, state, context):
        del state, context
        return None


class _PlanningModel:
    def __init__(self, *results: PlanningModelResult) -> None:
        self.results = deque(results)

    async def complete_once(self, state):
        del state
        return self.results.popleft()


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, model: LLMModel) -> None:
        self.model = model

    async def execute(self, statement):
        del statement
        return _Result(self.model)


def _session_factory(model: LLMModel):
    @asynccontextmanager
    async def factory():
        yield _DB(model)

    return factory


def test_plan_validator_accepts_the_three_execution_strategies() -> None:
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    candidates = frozenset({first, second, third})
    parallel = {
        "version": 1,
        "goal": "Parallel work",
        "execution_strategy": "parallel",
        "steps": [
            {
                "step_id": name,
                "agent_id": str(agent_id),
                "instruction": name,
                "depends_on_step_ids": [],
            }
            for name, agent_id in (("a", first), ("b", second), ("c", third))
        ],
    }
    sequential = {
        **parallel,
        "goal": "Sequential work",
        "execution_strategy": "sequential",
        "steps": [
            {**parallel["steps"][0]},
            {**parallel["steps"][1], "depends_on_step_ids": ["a"]},
            {**parallel["steps"][2], "depends_on_step_ids": ["b"]},
        ],
    }
    dependency = {
        **parallel,
        "goal": "DAG work",
        "execution_strategy": "dependency",
        "steps": [
            {**parallel["steps"][0]},
            {**parallel["steps"][1]},
            {**parallel["steps"][2], "depends_on_step_ids": ["a", "b"]},
        ],
    }

    assert validate_planning_output(parallel, candidate_agent_ids=candidates)[
        "execution_strategy"
    ] == "parallel"
    assert validate_planning_output(sequential, candidate_agent_ids=candidates)[
        "execution_strategy"
    ] == "sequential"
    assert validate_planning_output(dependency, candidate_agent_ids=candidates)[
        "execution_strategy"
    ] == "dependency"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_agent",
        "cycle",
        "strategy_mismatch",
        "missing_candidate",
    ],
)
def test_plan_validator_rejects_unsafe_or_incomplete_plans(mutation: str) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    raw = _plan(first, second)
    if mutation == "unknown_agent":
        raw["steps"][1]["agent_id"] = str(uuid.uuid4())
    elif mutation == "cycle":
        raw["steps"][0]["depends_on_step_ids"] = ["write"]
    elif mutation == "strategy_mismatch":
        raw["execution_strategy"] = "parallel"
    else:
        raw["steps"] = raw["steps"][:1]

    with pytest.raises(PlanningContractError, match="Agent|acyclic|parallel|mentioned"):
        validate_planning_output(raw, candidate_agent_ids=frozenset({first, second}))


@pytest.mark.asyncio
async def test_planning_model_uses_the_pinned_platform_model_without_tools() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=None,
        provider="openai",
        model="planning-model",
        api_key_encrypted="encrypted",
        label="Planning",
        enabled=True,
        max_output_tokens=2048,
        max_input_tokens=64_000,
    )
    state = _state((first, second), model_id=model.id)
    calls = []

    async def complete(model_arg, messages, **kwargs):
        calls.append((model_arg, messages, kwargs))
        return LLMCompletionStep(
            content=json.dumps(_plan(first, second)),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
        completion=complete,
    ).complete_once(state)

    assert result.plan is not None
    assert calls[0][0] is model
    assert calls[0][2] == {
        "tools": None,
        "agent_id": None,
        "supports_vision": False,
    }
    assert calls[0][1][0].role == "system"


@pytest.mark.asyncio
async def test_planning_model_accepts_a_pinned_model_from_the_run_tenant() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    model = LLMModel(
        id=uuid.UUID(state["registry"].model_id),
        tenant_id=uuid.UUID(state["registry"].tenant_id),
        provider="openai",
        model="tenant-planning-model",
        api_key_encrypted="encrypted",
        label="Tenant Planning",
        enabled=True,
        max_output_tokens=2048,
        max_input_tokens=64_000,
    )

    async def complete(_model, _messages, **_kwargs):
        return LLMCompletionStep(
            content=json.dumps(_plan(first, second)),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
        completion=complete,
    ).complete_once(state)

    assert result.plan is not None


@pytest.mark.asyncio
async def test_planning_model_rejects_a_cross_tenant_pinned_model() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    model = LLMModel(
        id=uuid.UUID(state["registry"].model_id),
        tenant_id=uuid.uuid4(),
        provider="openai",
        model="other-tenant-model",
        api_key_encrypted="encrypted",
        label="Other Tenant",
        enabled=True,
    )

    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
    ).complete_once(state)

    assert result.error_code == "planning_model_unavailable"


@pytest.mark.asyncio
async def test_invalid_plans_receive_two_repairs_then_fail_the_checkpoint() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    model = _PlanningModel(
        *(
            PlanningModelResult(
                error_code="invalid_plan",
                error_message="bad schema",
                raw_output="{}",
                retryable=True,
            )
            for _ in range(3)
        )
    )
    executor = PlanningRuntimeNodeExecutor(
        cancel_source=_CancelSource(),  # type: ignore[arg-type]
        model_service=model,  # type: ignore[arg-type]
        max_repairs=2,
    )

    for attempt in range(1, 4):
        update = await executor.execute("model", state, _context(state))
        state["lifecycle"] = update["lifecycle"]
        assert state["lifecycle"]["planning_attempt_count"] == attempt

    assert state["lifecycle"]["status"] == "failed"
    assert state["lifecycle"]["next_route"] == "terminal"
    assert state["lifecycle"]["error"] == {
        "code": "invalid_plan",
        "message": "bad schema",
    }


@pytest.mark.asyncio
async def test_child_results_unlock_dependencies_and_complete_the_root() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    plan = validate_planning_output(
        _plan(first, second),
        candidate_agent_ids=frozenset({first, second}),
    )
    state["lifecycle"].update(
        {
            "status": "waiting_agent",
            "next_route": "wait",
            "planning": plan,
            "waiting_request": {
                "waiting_type": "agent",
                "correlation_id": f"planning:{state['registry'].run_id}",
            },
        }
    )
    executor = PlanningRuntimeNodeExecutor(
        cancel_source=_CancelSource(),  # type: ignore[arg-type]
        model_service=_PlanningModel(),  # type: ignore[arg-type]
    )
    first_child = uuid.uuid4()
    update = await executor.execute(
        "wait",
        state,
        _context(state),
        resume_value={
            "resume_type": "agent_result",
            "correlation_id": f"planning:{state['registry'].run_id}",
            "payload": {
                "step_id": "research",
                "status": "completed",
                "child_run_id": str(first_child),
                "result_summary": {"summary": "evidence"},
                "error": None,
            },
        },
    )
    state["lifecycle"] = update["lifecycle"]

    assert state["lifecycle"]["status"] == "waiting_agent"
    ready = ready_plan_steps(cast(JsonObject, state["lifecycle"]["planning"]))
    assert [step["step_id"] for step in ready] == ["write"]

    second_child = uuid.uuid4()
    update = await executor.execute(
        "wait",
        state,
        _context(state),
        resume_value={
            "resume_type": "agent_result",
            "correlation_id": f"planning:{state['registry'].run_id}",
            "payload": {
                "step_id": "write",
                "status": "completed",
                "child_run_id": str(second_child),
                "result_summary": {"summary": "answer"},
                "error": None,
            },
        },
    )

    assert update["lifecycle"]["status"] == "completed"
    assert update["lifecycle"]["next_route"] == "terminal"
    assert update["lifecycle"]["result_summary"]["steps"] == [
        {
            "step_id": "research",
            "agent_id": str(first),
            "status": "completed",
            "child_run_id": str(first_child),
        },
        {
            "step_id": "write",
            "agent_id": str(second),
            "status": "completed",
            "child_run_id": str(second_child),
        },
    ]


@pytest.mark.asyncio
async def test_failed_dependency_blocks_descendants_and_fails_the_root() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    state["lifecycle"].update(
        {
            "status": "waiting_agent",
            "next_route": "wait",
            "planning": validate_planning_output(
                _plan(first, second),
                candidate_agent_ids=frozenset({first, second}),
            ),
            "waiting_request": {
                "waiting_type": "agent",
                "correlation_id": f"planning:{state['registry'].run_id}",
            },
        }
    )
    executor = PlanningRuntimeNodeExecutor(
        cancel_source=_CancelSource(),  # type: ignore[arg-type]
        model_service=_PlanningModel(),  # type: ignore[arg-type]
    )
    update = await executor.execute(
        "wait",
        state,
        _context(state),
        resume_value={
            "resume_type": "agent_result",
            "correlation_id": f"planning:{state['registry'].run_id}",
            "payload": {
                "step_id": "research",
                "status": "failed",
                "child_run_id": str(uuid.uuid4()),
                "result_summary": None,
                "error": {"code": "child_failed"},
            },
        },
    )

    assert update["lifecycle"]["status"] == "failed"
    planning = cast(JsonObject, update["lifecycle"]["planning"])
    assert cast(list[JsonObject], planning["steps"])[1]["status"] == "blocked"
    assert update["lifecycle"]["error"]["code"] == "planning_child_failed"
