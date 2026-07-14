"""Checkpoint-owned multi-Agent planning model and deterministic transitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Protocol, cast
import uuid

from sqlalchemy import select

from app.models.llm import LLMModel
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
)
from app.services.agent_runtime.node_executor import RuntimeCancelSource
from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeLifecycle,
    RuntimeNodeExecutor,
    RuntimeNodeName,
    RuntimeStateUpdate,
)
from app.services.llm.client import LLMMessage
from app.services.llm.single_step import LLMCompletionStep, complete_llm_once
from app.services.llm.utils import get_max_tokens


_PLANNING_ROLE = "group_planning"
_PLAN_VERSION = 1
_EXECUTION_STRATEGIES = frozenset({"parallel", "sequential", "dependency"})
_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_STEP_TERMINAL = frozenset({"completed", "failed", "cancelled", "blocked"})
_MAX_PLAN_STEPS = 50
_MAX_APPLIED_COMMAND_IDS = 64

_SYSTEM_PROMPT = """You are Clawith's internal multi-Agent planning component.
Return exactly one JSON object and no Markdown. Never call tools and never do the work yourself.
Use only candidate agent_id values supplied by the caller.
Schema:
{
  "version": 1,
  "goal": "collaboration goal",
  "execution_strategy": "parallel | sequential | dependency",
  "steps": [
    {
      "step_id": "stable-id",
      "agent_id": "candidate UUID",
      "instruction": "work assigned to that Agent",
      "depends_on_step_ids": []
    }
  ]
}
parallel means every dependency list is empty. sequential means each step after the first depends only on the immediately previous step. dependency is any other acyclic dependency graph. Preserve explicit user assignments and ordering."""


class PlanningContractError(RuntimeError):
    """Planning data or transitions violate the checkpoint contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlanningModelResult:
    """One side-effect-free planning call outcome."""

    plan: JsonObject | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw_output: str | None = None
    retryable: bool = False


class PlanningCompletionPort(Protocol):
    async def __call__(
        self,
        model: LLMModel,
        messages: list[LLMMessage],
        *,
        tools: list[dict] | None = None,
        agent_id: uuid.UUID | None = None,
        supports_vision: bool = False,
    ) -> LLMCompletionStep: ...


def _required_text(value: object, *, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningContractError("invalid_plan", f"{field} must not be blank")
    if len(value) > max_length:
        raise PlanningContractError(
            "invalid_plan",
            f"{field} exceeds {max_length} characters",
        )
    return value.strip()


def _candidate_agent_ids(state: RuntimeGraphState) -> frozenset[uuid.UUID]:
    candidates = state["snapshots"].initial_input.get("candidate_agents")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise PlanningContractError(
            "invalid_planning_input",
            "candidate_agents must be an array",
        )
    resolved: set[uuid.UUID] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise PlanningContractError(
                "invalid_planning_input",
                "candidate_agents entries must be objects",
            )
        try:
            agent_id = uuid.UUID(str(candidate.get("agent_id")))
        except (TypeError, ValueError) as exc:
            raise PlanningContractError(
                "invalid_planning_input",
                "candidate agent_id must be a UUID",
            ) from exc
        resolved.add(agent_id)
    if len(resolved) < 2:
        raise PlanningContractError(
            "invalid_planning_input",
            "Planning requires at least two distinct candidate Agents",
        )
    return frozenset(resolved)


def _has_cycle(steps: list[JsonObject]) -> bool:
    dependencies = {
        cast(str, step["step_id"]): set(cast(list[str], step["depends_on_step_ids"]))
        for step in steps
    }
    ready = [step_id for step_id, values in dependencies.items() if not values]
    visited = 0
    while ready:
        completed = ready.pop()
        visited += 1
        for step_id, values in dependencies.items():
            if completed not in values:
                continue
            values.remove(completed)
            if not values:
                ready.append(step_id)
    return visited != len(dependencies)


def _is_sequential(steps: list[JsonObject]) -> bool:
    for index, step in enumerate(steps):
        dependencies = cast(list[str], step["depends_on_step_ids"])
        expected = [] if index == 0 else [cast(str, steps[index - 1]["step_id"])]
        if dependencies != expected:
            return False
    return True


def validate_planning_output(
    raw: object,
    *,
    candidate_agent_ids: frozenset[uuid.UUID],
) -> JsonObject:
    """Validate and normalize model output before it can enter a checkpoint."""
    if not isinstance(raw, Mapping):
        raise PlanningContractError("invalid_plan", "Planning output must be an object")
    if raw.get("version") != _PLAN_VERSION:
        raise PlanningContractError("invalid_plan", "Planning output version must be 1")
    goal = _required_text(raw.get("goal"), field="goal", max_length=10_000)
    strategy = raw.get("execution_strategy")
    if strategy not in _EXECUTION_STRATEGIES:
        raise PlanningContractError(
            "invalid_plan",
            "execution_strategy must be parallel, sequential, or dependency",
        )
    raw_steps = raw.get("steps")
    if (
        not isinstance(raw_steps, Sequence)
        or isinstance(raw_steps, (str, bytes, bytearray))
        or not raw_steps
        or len(raw_steps) > _MAX_PLAN_STEPS
    ):
        raise PlanningContractError(
            "invalid_plan",
            f"steps must contain between 1 and {_MAX_PLAN_STEPS} entries",
        )

    steps: list[JsonObject] = []
    step_ids: set[str] = set()
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            raise PlanningContractError("invalid_plan", "each step must be an object")
        step_id = _required_text(
            raw_step.get("step_id"),
            field="step_id",
            max_length=64,
        )
        if not _STEP_ID.fullmatch(step_id):
            raise PlanningContractError(
                "invalid_plan",
                "step_id may contain only letters, numbers, underscore, and hyphen",
            )
        if step_id in step_ids:
            raise PlanningContractError("invalid_plan", "step_id values must be unique")
        step_ids.add(step_id)
        try:
            agent_id = uuid.UUID(str(raw_step.get("agent_id")))
        except (TypeError, ValueError) as exc:
            raise PlanningContractError("invalid_plan", "step agent_id must be a UUID") from exc
        if agent_id not in candidate_agent_ids:
            raise PlanningContractError(
                "invalid_plan",
                "step agent_id is not one of the mentioned candidate Agents",
            )
        instruction = _required_text(
            raw_step.get("instruction"),
            field="instruction",
            max_length=20_000,
        )
        dependencies = raw_step.get("depends_on_step_ids", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) or not dependency for dependency in dependencies
        ):
            raise PlanningContractError(
                "invalid_plan",
                "depends_on_step_ids must be an array of step IDs",
            )
        if len(dependencies) != len(set(dependencies)):
            raise PlanningContractError(
                "invalid_plan",
                "depends_on_step_ids must not contain duplicates",
            )
        steps.append(
            {
                "step_id": step_id,
                "agent_id": str(agent_id),
                "instruction": instruction,
                "depends_on_step_ids": list(dependencies),
                "status": "pending",
                "child_run_id": None,
                "result_summary": None,
                "error": None,
            }
        )

    for step in steps:
        step_id = cast(str, step["step_id"])
        dependencies = cast(list[str], step["depends_on_step_ids"])
        if step_id in dependencies or any(dependency not in step_ids for dependency in dependencies):
            raise PlanningContractError(
                "invalid_plan",
                "step dependencies must reference other steps in the same plan",
            )
    if _has_cycle(steps):
        raise PlanningContractError("invalid_plan", "step dependencies must be acyclic")

    planned_agent_ids = {
        uuid.UUID(cast(str, step["agent_id"]))
        for step in steps
    }
    if planned_agent_ids != candidate_agent_ids:
        raise PlanningContractError(
            "invalid_plan",
            "every mentioned candidate Agent must receive at least one step",
        )

    all_parallel = all(not cast(list[str], step["depends_on_step_ids"]) for step in steps)
    sequential = _is_sequential(steps)
    if strategy == "parallel" and not all_parallel:
        raise PlanningContractError(
            "invalid_plan",
            "parallel strategy cannot contain dependencies",
        )
    if strategy == "sequential" and not sequential:
        raise PlanningContractError(
            "invalid_plan",
            "sequential strategy must form one ordered dependency chain",
        )
    if strategy == "dependency" and (all_parallel or sequential):
        raise PlanningContractError(
            "invalid_plan",
            "dependency strategy must describe a non-parallel, non-linear DAG",
        )
    return {
        "version": _PLAN_VERSION,
        "goal": goal,
        "execution_strategy": cast(str, strategy),
        "steps": steps,
    }


def _parse_json_output(content: str | None) -> object:
    if content is None or not content.strip():
        raise PlanningContractError("invalid_plan", "Planning model returned no content")
    value = content.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1])
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise PlanningContractError(
            "invalid_plan",
            "Planning model output is not valid JSON",
        ) from exc


class PlanningModelService:
    """Call the pinned platform model once with no tools or fallback model."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        completion: PlanningCompletionPort = complete_llm_once,  # type: ignore[assignment]
    ) -> None:
        self._session_factory = session_factory
        self._completion = completion

    async def _load_model(self, state: RuntimeGraphState) -> LLMModel:
        try:
            model_id = uuid.UUID(state["registry"].model_id)
            tenant_id = uuid.UUID(state["registry"].tenant_id)
        except ValueError as exc:
            raise PlanningContractError(
                "planning_model_unavailable",
                "Planning Run has an invalid pinned model",
            ) from exc
        async with self._session_factory() as db:
            result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
            model = result.scalar_one_or_none()
        if model is None or not model.enabled or model.tenant_id not in {None, tenant_id}:
            raise PlanningContractError(
                "planning_model_unavailable",
                "Pinned Planning model is not enabled for the Run tenant",
            )
        try:
            ModelCapabilityResolver.request_input_limit(
                model,
                requested_max_output_tokens=get_max_tokens(
                    model.provider,
                    model.model,
                    model.max_output_tokens,
                ),
            )
        except ModelCapabilityError as exc:
            raise PlanningContractError(
                "planning_model_capability_invalid",
                "Pinned Planning model has no safe input budget",
            ) from exc
        return model

    async def complete_once(self, state: RuntimeGraphState) -> PlanningModelResult:
        try:
            candidates = _candidate_agent_ids(state)
            model = await self._load_model(state)
        except PlanningContractError as exc:
            return PlanningModelResult(
                error_code=exc.code,
                error_message=str(exc),
                retryable=False,
            )

        planning_state = state["lifecycle"].get("planning")
        repair_context = None
        if isinstance(planning_state, Mapping) and planning_state.get("last_error"):
            repair_context = {
                "previous_output": planning_state.get("last_raw_output"),
                "validation_error": planning_state.get("last_error"),
            }
        request = {
            "user_goal": state["registry"].goal,
            "candidate_agents": state["snapshots"].initial_input.get("candidate_agents", []),
            "explicit_user_plan_has_priority": True,
            "repair": repair_context,
        }
        messages = [
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=json.dumps(request, ensure_ascii=False, sort_keys=True),
            ),
        ]
        try:
            completion = await self._completion(
                model,
                messages,
                tools=None,
                agent_id=None,
                supports_vision=False,
            )
        except Exception:
            return PlanningModelResult(
                error_code="planning_model_call_failed",
                error_message="Planning model call failed",
                retryable=True,
            )
        if completion.tool_calls:
            return PlanningModelResult(
                error_code="invalid_plan",
                error_message="Planning model attempted to call a tool",
                raw_output=completion.content,
                retryable=True,
            )
        try:
            plan = validate_planning_output(
                _parse_json_output(completion.content),
                candidate_agent_ids=candidates,
            )
        except PlanningContractError as exc:
            return PlanningModelResult(
                error_code=exc.code,
                error_message=str(exc),
                raw_output=completion.content,
                retryable=True,
            )
        return PlanningModelResult(plan=plan, raw_output=completion.content)


def checkpoint_plan(state: RuntimeGraphState) -> JsonObject:
    planning = state["lifecycle"].get("planning")
    if not isinstance(planning, Mapping):
        raise PlanningContractError(
            "invalid_planning_checkpoint",
            "Planning checkpoint has no plan state",
        )
    steps = planning.get("steps")
    if not isinstance(steps, list) or any(not isinstance(step, Mapping) for step in steps):
        raise PlanningContractError(
            "invalid_planning_checkpoint",
            "Planning checkpoint steps are malformed",
        )
    return cast(JsonObject, dict(planning))


def ready_plan_steps(plan: Mapping[str, object]) -> tuple[JsonObject, ...]:
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list):
        raise PlanningContractError(
            "invalid_planning_checkpoint",
            "Planning checkpoint steps are malformed",
        )
    steps = [cast(Mapping[str, object], step) for step in raw_steps if isinstance(step, Mapping)]
    if len(steps) != len(raw_steps):
        raise PlanningContractError(
            "invalid_planning_checkpoint",
            "Planning checkpoint steps are malformed",
        )
    status_by_id = {str(step.get("step_id")): step.get("status") for step in steps}
    ready = []
    for step in steps:
        if step.get("status") != "pending":
            continue
        dependencies = step.get("depends_on_step_ids")
        if not isinstance(dependencies, list):
            raise PlanningContractError(
                "invalid_planning_checkpoint",
                "Planning step dependencies are malformed",
            )
        if all(status_by_id.get(str(dependency)) == "completed" for dependency in dependencies):
            ready.append(cast(JsonObject, dict(step)))
    return tuple(ready)


def _append_command_id(lifecycle: Mapping[str, object], command_id: str) -> list[str]:
    values = lifecycle.get("last_applied_command_ids", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise PlanningContractError(
            "invalid_checkpoint_command_ids",
            "Planning checkpoint command IDs are malformed",
        )
    return [* [value for value in values if value != command_id], command_id][
        -_MAX_APPLIED_COMMAND_IDS:
    ]


def _waiting_request(run_id: str) -> JsonObject:
    return {
        "waiting_type": "agent",
        "correlation_id": f"planning:{run_id}",
        "reason": "Waiting for planned Agent steps to finish.",
    }


def _step_result_summary(steps: list[JsonObject]) -> JsonObject:
    artifact_refs: list[JsonValue] = []
    for step in steps:
        result = step.get("result_summary")
        if isinstance(result, Mapping):
            refs = result.get("artifact_refs")
            if isinstance(refs, list):
                artifact_refs.extend(cast(list[JsonValue], refs))
    return {
        "summary": "Group planning coordination finished.",
        "steps": [
            {
                "step_id": step.get("step_id"),
                "agent_id": step.get("agent_id"),
                "status": step.get("status"),
                "child_run_id": step.get("child_run_id"),
            }
            for step in steps
        ],
        "artifact_refs": artifact_refs,
    }


class PlanningRuntimeNodeExecutor:
    """Advance only the Planning Graph's checkpoint-owned orchestration state."""

    def __init__(
        self,
        *,
        cancel_source: RuntimeCancelSource,
        model_service: PlanningModelService,
        max_repairs: int = 2,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must not be negative")
        self._cancel_source = cancel_source
        self._model_service = model_service
        self._max_repairs = max_repairs

    @staticmethod
    def _require_planning_run(state: RuntimeGraphState) -> None:
        registry = state["registry"]
        if registry.system_role != _PLANNING_ROLE or registry.agent_id is not None:
            raise PlanningContractError(
                "planning_identity_mismatch",
                "Planning executor requires the group_planning system Run",
            )

    async def _control(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        if lifecycle["status"] in {"completed", "failed", "cancelled"}:
            lifecycle["next_route"] = "terminal"
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        cancel = await self._cancel_source.get_cancel(state, context)
        if cancel is not None:
            lifecycle.update(
                {
                    "status": "cancelled",
                    "next_route": "terminal",
                    "reason": cancel.reason or "cancelled_by_command",
                    "last_applied_command_ids": _append_command_id(
                        lifecycle,
                        cancel.command_id,
                    ),
                    "waiting_request": None,
                }
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _model(self, state: RuntimeGraphState) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        attempt = lifecycle.get("planning_attempt_count", 0)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise PlanningContractError(
                "invalid_planning_checkpoint",
                "planning_attempt_count must be a non-negative integer",
            )
        attempt += 1
        result = await self._model_service.complete_once(state)
        lifecycle["planning_attempt_count"] = attempt
        if result.plan is not None:
            lifecycle.update(
                {
                    "status": "waiting_agent",
                    "next_route": "wait",
                    "planning": dict(result.plan),
                    "waiting_request": _waiting_request(state["registry"].run_id),
                    "error": None,
                }
            )
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

        error_code = result.error_code or "planning_failed"
        error_message = result.error_message or "Planning did not produce a valid plan"
        lifecycle["planning"] = {
            "repair_count": attempt,
            "last_error": error_message,
            "last_raw_output": result.raw_output,
        }
        if result.retryable and attempt <= self._max_repairs:
            lifecycle.update(
                {
                    "status": "running",
                    "next_route": "model",
                    "reason": "planning_repair_required",
                }
            )
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        lifecycle.update(
            {
                "status": "failed",
                "next_route": "terminal",
                "reason": error_code,
                "error": {"code": error_code, "message": error_message},
                "waiting_request": None,
            }
        )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    @staticmethod
    def _apply_child_result(
        state: RuntimeGraphState,
        resume_value: JsonValue | None,
    ) -> RuntimeStateUpdate:
        if not isinstance(resume_value, Mapping):
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning resume value must be an object",
            )
        if resume_value.get("resume_type") != "agent_result":
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning only accepts agent_result resumes",
            )
        expected_correlation = f"planning:{state['registry'].run_id}"
        if resume_value.get("correlation_id") != expected_correlation:
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning resume correlation does not match the root Run",
            )
        payload = resume_value.get("payload")
        if not isinstance(payload, Mapping):
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning resume payload must be an object",
            )
        step_id = payload.get("step_id")
        status = payload.get("status")
        child_run_id = payload.get("child_run_id")
        if not isinstance(step_id, str) or status not in {"completed", "failed", "cancelled"}:
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning resume requires a step ID and terminal child status",
            )
        if child_run_id is not None:
            try:
                uuid.UUID(str(child_run_id))
            except (TypeError, ValueError) as exc:
                raise PlanningContractError(
                    "invalid_planning_resume",
                    "Planning child_run_id must be a UUID",
                ) from exc

        lifecycle = dict(state["lifecycle"])
        plan = checkpoint_plan(state)
        steps = [dict(cast(Mapping[str, JsonValue], step)) for step in cast(list, plan["steps"])]
        target = next((step for step in steps if step.get("step_id") == step_id), None)
        if target is None:
            raise PlanningContractError(
                "invalid_planning_resume",
                "Planning resume references an unknown step",
            )
        current_status = target.get("status")
        if current_status in _STEP_TERMINAL:
            if current_status != status or target.get("child_run_id") != child_run_id:
                raise PlanningContractError(
                    "planning_result_conflict",
                    "Planning step already has a different terminal result",
                )
        else:
            target.update(
                {
                    "status": cast(str, status),
                    "child_run_id": str(child_run_id) if child_run_id is not None else None,
                    "result_summary": payload.get("result_summary"),
                    "error": payload.get("error"),
                }
            )

        changed = True
        while changed:
            changed = False
            status_by_id = {str(step["step_id"]): step.get("status") for step in steps}
            for step in steps:
                if step.get("status") != "pending":
                    continue
                dependencies = cast(list[str], step.get("depends_on_step_ids", []))
                if any(
                    status_by_id.get(dependency) in {"failed", "cancelled", "blocked"}
                    for dependency in dependencies
                ):
                    step.update(
                        {
                            "status": "blocked",
                            "error": {
                                "code": "dependency_failed",
                                "message": "A dependency did not complete successfully.",
                            },
                        }
                    )
                    changed = True

        plan["steps"] = steps
        lifecycle["planning"] = plan
        if all(step.get("status") in _STEP_TERMINAL for step in steps):
            success = all(step.get("status") == "completed" for step in steps)
            lifecycle.update(
                {
                    "status": "completed" if success else "failed",
                    "next_route": "terminal",
                    "waiting_request": None,
                    "final_answer": "Group collaboration completed." if success else None,
                    "result_summary": _step_result_summary(steps),
                    "error": (
                        None
                        if success
                        else {
                            "code": "planning_child_failed",
                            "message": "One or more planned Agent steps did not complete.",
                        }
                    ),
                }
            )
        else:
            lifecycle.update(
                {
                    "status": "waiting_agent",
                    "next_route": "wait",
                    "waiting_request": _waiting_request(state["registry"].run_id),
                }
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        self._require_planning_run(state)
        if node == "control_guard":
            return await self._control(state, context)
        if node == "model":
            return await self._model(state)
        if node == "wait":
            return self._apply_child_result(state, resume_value)
        if node == "terminal":
            return {"lifecycle": dict(state["lifecycle"])}
        raise PlanningContractError(
            "invalid_planning_route",
            f"Planning Graph cannot execute {node}",
        )


class RuntimeNodeExecutorRouter:
    """Select a node implementation from immutable checkpoint identity."""

    def __init__(
        self,
        *,
        agent_executor: RuntimeNodeExecutor,
        planning_executor: RuntimeNodeExecutor,
    ) -> None:
        self._agent_executor = agent_executor
        self._planning_executor = planning_executor

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        executor = (
            self._planning_executor
            if state["registry"].system_role == _PLANNING_ROLE
            else self._agent_executor
        )
        return await executor.execute(
            node,
            state,
            context,
            resume_value=resume_value,
        )


__all__ = [
    "PlanningContractError",
    "PlanningModelResult",
    "PlanningModelService",
    "PlanningRuntimeNodeExecutor",
    "RuntimeNodeExecutorRouter",
    "checkpoint_plan",
    "ready_plan_steps",
    "validate_planning_output",
]
