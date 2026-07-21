"""Planning v2 checkpoint contract and terminal transition tests."""

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
    checkpoint_plan,
    validate_planning_output,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RunInputSnapshots,
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


def _state(agent_ids: tuple[uuid.UUID, ...]) -> RuntimeGraphState:
    return {
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "candidate_agents": [
                    _candidate(agent_id, f"Agent {index}") for index, agent_id in enumerate(agent_ids, start=1)
                ]
            },
        ),
        "messages": [],
        "lifecycle": {
            "status": "running",
            "next_route": "model",
            "pending_tool_calls": [],
        },
    }


def _context(
    *,
    model_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=str(tenant_id or uuid.uuid4()),
        run_id=str(run_id or uuid.uuid4()),
        command_id=str(uuid.uuid4()),
        executor=cast(RuntimeNodeExecutor, object()),
        goal="Research the topic, then write the answer",
        run_kind="orchestration",
        source_type="chat",
        model_id=str(model_id or uuid.uuid4()),
        graph_name="runtime_group_planning",
        graph_version="v1",
        agent_id=None,
        session_id=str(uuid.uuid4()),
        system_role="group_planning",
    )


def _plan(
    first: uuid.UUID,
    second: uuid.UUID | None = None,
    *,
    mode: str = "advisory",
) -> dict:
    entries = [
        {
            "agent_id": str(first),
            "instruction": "Research the evidence",
        }
    ]
    if second is not None:
        entries.append(
            {
                "agent_id": str(second),
                "instruction": "Review the initial evidence",
            }
        )
    return {
        "version": 2,
        "mode": mode,
        "goal": "Produce one grounded answer",
        "plan_prompt": (
            "Research the request, publish each handoff in the group, and stop when the requested answer is grounded."
        ),
        "entry_steps": entries,
    }


class _CancelSource:
    async def get_cancel(self, state, context):
        del state, context
        return None


class _PlanningModel:
    def __init__(self, *results: PlanningModelResult) -> None:
        self.results = deque(results)

    async def complete_once(self, state, context):
        del state, context
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


def test_plan_validator_accepts_an_entry_subset_without_inventing_a_dag() -> None:
    first, second, non_entry = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    raw = _plan(first, second, mode="enforced")

    plan = validate_planning_output(
        raw,
        candidate_agent_ids=frozenset({first, second, non_entry}),
    )

    assert plan == raw
    assert [entry["agent_id"] for entry in plan["entry_steps"]] == [
        str(first),
        str(second),
    ]
    assert "steps" not in plan
    assert "execution_strategy" not in plan


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy_v1",
        "unknown_agent",
        "duplicate_agent",
        "blank_goal",
        "blank_plan_prompt",
        "blank_instruction",
        "invalid_mode",
        "unknown_field",
        "too_many_entries",
    ],
)
def test_plan_validator_rejects_non_v2_or_nonstructural_input(mutation: str) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    candidates = {first, second}
    raw = _plan(first, second)
    if mutation == "legacy_v1":
        raw = {
            "version": 1,
            "goal": "Old plan",
            "execution_strategy": "parallel",
            "steps": [],
        }
    elif mutation == "unknown_agent":
        raw["entry_steps"][1]["agent_id"] = str(uuid.uuid4())
    elif mutation == "duplicate_agent":
        raw["entry_steps"][1]["agent_id"] = str(first)
    elif mutation == "blank_goal":
        raw["goal"] = "  "
    elif mutation == "blank_plan_prompt":
        raw["plan_prompt"] = ""
    elif mutation == "blank_instruction":
        raw["entry_steps"][0]["instruction"] = " "
    elif mutation == "invalid_mode":
        raw["mode"] = "dependency"
    elif mutation == "unknown_field":
        raw["execution_strategy"] = "parallel"
    else:
        many_agents = tuple(uuid.uuid4() for _ in range(51))
        candidates.update(many_agents)
        raw["entry_steps"] = [
            {"agent_id": str(agent_id), "instruction": f"Entry {index}"} for index, agent_id in enumerate(many_agents)
        ]

    with pytest.raises(PlanningContractError):
        validate_planning_output(raw, candidate_agent_ids=frozenset(candidates))


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
    state = _state((first, second))
    calls = []

    async def complete(model_arg, messages, **kwargs):
        calls.append((model_arg, messages, kwargs))
        return LLMCompletionStep(
            content=json.dumps(_plan(first)),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
        completion=complete,
    ).complete_once(state, _context(model_id=model.id))

    assert result.plan == _plan(first)
    assert calls[0][0] is model
    assert calls[0][2] == {
        "tools": None,
        "agent_id": None,
        "supports_vision": False,
    }
    planning_prompt = str(calls[0][1][0].content)
    assert '"version": 2' in planning_prompt
    assert '"entry_steps"' in planning_prompt
    assert "advisory" in planning_prompt
    assert "enforced" in planning_prompt
    assert "depends_on_step_ids" not in planning_prompt
    assert "digital employee in Clawith" not in planning_prompt
    assert "call `finish`" not in planning_prompt
    assert "call `wait`" not in planning_prompt


@pytest.mark.asyncio
async def test_planning_model_accepts_a_model_owned_by_the_group_tenant() -> None:
    tenant_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
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
            content=json.dumps(_plan(first)),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
        completion=complete,
    ).complete_once(
        _state((first, second)),
        _context(model_id=model.id, tenant_id=tenant_id),
    )

    assert result.plan == _plan(first)


@pytest.mark.asyncio
async def test_planning_model_rejects_a_model_owned_by_another_tenant() -> None:
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider="openai",
        model="foreign-planning-model",
        api_key_encrypted="encrypted",
        label="Foreign Planning",
        enabled=True,
        max_output_tokens=2048,
        max_input_tokens=64_000,
    )
    result = await PlanningModelService(
        session_factory=_session_factory(model),  # type: ignore[arg-type]
    ).complete_once(
        _state((uuid.uuid4(), uuid.uuid4())),
        _context(model_id=model.id, tenant_id=uuid.uuid4()),
    )

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
    context = _context()

    for attempt in range(1, 4):
        update = await executor.execute("model", state, context)
        state["lifecycle"] = update["lifecycle"]
        assert state["lifecycle"]["planning_attempt_count"] == attempt

    assert state["lifecycle"]["status"] == "failed"
    assert state["lifecycle"]["next_route"] == "terminal"
    assert state["lifecycle"]["error"] == {
        "code": "invalid_plan",
        "message": "bad schema",
    }


@pytest.mark.asyncio
async def test_valid_plan_completes_without_waiting_and_freezes_the_exact_v2_plan() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    plan = validate_planning_output(
        _plan(first, mode="enforced"),
        candidate_agent_ids=frozenset({first, second}),
    )
    executor = PlanningRuntimeNodeExecutor(
        cancel_source=_CancelSource(),  # type: ignore[arg-type]
        model_service=_PlanningModel(PlanningModelResult(plan=plan)),  # type: ignore[arg-type]
    )

    update = await executor.execute("model", state, _context())

    assert update["lifecycle"]["status"] == "completed"
    assert update["lifecycle"]["next_route"] == "terminal"
    assert update["lifecycle"]["planning"] == plan
    assert update["lifecycle"]["waiting_request"] is None
    assert update["lifecycle"]["error"] is None


def test_checkpoint_plan_revalidates_the_frozen_candidate_scope() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    state["lifecycle"]["planning"] = _plan(first)

    assert checkpoint_plan(state) == _plan(first)

    state["lifecycle"]["planning"] = _plan(uuid.uuid4())
    with pytest.raises(PlanningContractError, match="candidate"):
        checkpoint_plan(state)


@pytest.mark.asyncio
async def test_planning_executor_has_no_child_resume_path() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    state = _state((first, second))
    plan = validate_planning_output(
        _plan(first),
        candidate_agent_ids=frozenset({first, second}),
    )
    state["lifecycle"].update(
        {
            "status": "completed",
            "next_route": "terminal",
            "planning": plan,
            "waiting_request": None,
        }
    )
    executor = PlanningRuntimeNodeExecutor(
        cancel_source=_CancelSource(),  # type: ignore[arg-type]
        model_service=_PlanningModel(),  # type: ignore[arg-type]
    )

    with pytest.raises(PlanningContractError, match="cannot execute wait"):
        await executor.execute(
            "wait",
            state,
            _context(),
            resume_value={
                "resume_type": "agent_result",
                "correlation_id": "planning:legacy",
                "payload": {},
            },
        )
