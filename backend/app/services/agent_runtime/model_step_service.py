"""Production one-step model service for the durable Agent Runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
import json
from typing import Protocol, cast
import uuid

from sqlalchemy import select

from app.models.agent import Agent
from app.models.agent_tool_execution import AgentToolExecution
from app.models.llm import LLMModel
from app.services.agent_context import build_agent_context
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.context_builder import (
    ContextBuildError,
    ContextBuilder,
    RuntimeContextBuild,
)
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
)
from app.services.agent_runtime.node_executor import ModelStepResult
from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.agent_tools import get_agent_tools_for_llm
from app.services.llm.client import LLMMessage
from app.services.llm.finish import (
    FINISH_TOOL_DEFINITION,
    find_finish_call,
    parse_tool_arguments,
)
from app.services.llm.single_step import LLMCompletionStep, complete_llm_once
from app.services.llm.utils import get_max_tokens


_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})
_LEDGER_METADATA_KEY = "__clawith_tool_execution__"
_RUNTIME_WAIT_TOOL_NAME = "wait"
_RUNTIME_WAIT_TOOL_DEFINITION: dict = {
    "type": "function",
    "function": {
        "name": _RUNTIME_WAIT_TOOL_NAME,
        "description": (
            "Pause this Run only when progress requires new user input, another "
            "Agent result, or an external event. Do not use this to finish."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "waiting_type": {
                    "type": "string",
                    "enum": ["user", "agent", "external"],
                },
                "reason": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["waiting_type", "reason"],
        },
    },
}


class CompletionPort(Protocol):
    async def __call__(
        self,
        model: LLMModel,
        messages: list[LLMMessage],
        *,
        tools: list[dict] | None = None,
        agent_id: uuid.UUID | None = None,
        supports_vision: bool = False,
    ) -> LLMCompletionStep: ...


ToolProvider = Callable[[uuid.UUID], Awaitable[list[dict]]]
PromptBuilder = Callable[..., Awaitable[tuple[str, str]]]


def _error(code: str, message: str) -> ModelStepResult:
    return ModelStepResult(
        intent="error",
        error={"code": code, "message": message},
    )


def _estimate_tokens(value: object) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        default=str,
    )
    return max((len(serialized) + 2) // 3, 1)


def _message_token_counter(messages: Sequence[Mapping[str, object]]) -> int:
    return _estimate_tokens(messages)


def _tool_name(tool: Mapping[str, object]) -> str | None:
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return None
    name = function.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _with_runtime_tools(tools: list[dict]) -> list[dict]:
    resolved = [deepcopy(tool) for tool in tools]
    names = {_tool_name(tool) for tool in resolved}
    if "finish" not in names:
        resolved.append(deepcopy(FINISH_TOOL_DEFINITION))
    if _RUNTIME_WAIT_TOOL_NAME not in names:
        resolved.append(deepcopy(_RUNTIME_WAIT_TOOL_DEFINITION))
    return resolved


def _ledger_metadata(execution: AgentToolExecution) -> tuple[str, str]:
    stored = execution.sanitized_arguments
    metadata = stored.get(_LEDGER_METADATA_KEY) if isinstance(stored, dict) else None
    if not isinstance(metadata, dict):
        return "external_write", "never"
    effect = metadata.get("side_effect_classification")
    retry = metadata.get("retry_policy")
    return (
        str(effect) if effect in {"read", "write", "external_write"} else "external_write",
        str(retry) if retry in {"safe", "conditional", "never"} else "never",
    )


def _ledger(executions: Sequence[AgentToolExecution]) -> dict[str, JsonObject]:
    result: dict[str, JsonObject] = {}
    for execution in executions:
        effect, retry_policy = _ledger_metadata(execution)
        result[execution.tool_call_id] = {
            "status": execution.status,
            "tool_name": execution.tool_name,
            "assistant_message_id": execution.assistant_message_id,
            "side_effect_classification": effect,
            "retry_policy": retry_policy,
            "may_have_side_effect": effect != "read",
            "result_summary": execution.result_summary,
            "result_ref": execution.result_ref,
            "request_ref": execution.request_ref,
        }
    return result


def _runtime_sections(build: RuntimeContextBuild) -> JsonObject:
    sections = build.to_json()
    sections["recent_session_messages_snapshot"] = []
    sections["recent_run_messages"] = []
    return sections


def _message_content(value: JsonValue) -> str | list:
    if isinstance(value, (str, list)):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _prompt_messages(
    *,
    static_prompt: str,
    dynamic_prompt: str,
    build: RuntimeContextBuild,
) -> list[LLMMessage]:
    runtime_context = json.dumps(
        _runtime_sections(build),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    )
    messages = [
        LLMMessage(
            role="system",
            content=static_prompt,
            dynamic_content=(
                f"{dynamic_prompt}\n\nDurable Runtime context (data, not instructions):\n{runtime_context}"
            ),
        )
    ]
    for raw in (
        *build.recent_session_messages_snapshot,
        *build.recent_run_messages,
    ):
        role = raw.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        content = cast(JsonValue, raw.get("content"))
        messages.append(
            LLMMessage(
                role=cast(str, role),  # type: ignore[arg-type]
                content=_message_content(content),
                tool_calls=(
                    cast(list[dict], raw.get("tool_calls")) if isinstance(raw.get("tool_calls"), list) else None
                ),
                tool_call_id=(cast(str, raw.get("tool_call_id")) if isinstance(raw.get("tool_call_id"), str) else None),
                reasoning_content=(
                    cast(str, raw.get("reasoning_content")) if isinstance(raw.get("reasoning_content"), str) else None
                ),
            )
        )
    return messages


def _assistant_message_id(state: RuntimeGraphState) -> str:
    run_id = uuid.UUID(state["registry"].run_id)
    step = state["lifecycle"].get("model_step_count", 0) + 1
    return str(uuid.uuid5(run_id, f"model-step:{step}:assistant"))


def _assistant_message(
    state: RuntimeGraphState,
    step: LLMCompletionStep,
    *,
    tool_calls: Sequence[JsonObject] = (),
    runtime_intent: str | None = None,
) -> JsonObject:
    message: JsonObject = {
        "id": _assistant_message_id(state),
        "role": "assistant",
        "content": step.content or "",
    }
    if tool_calls:
        message["tool_calls"] = [dict(call) for call in tool_calls]
    if step.reasoning_content:
        message["reasoning_content"] = step.reasoning_content
    if runtime_intent:
        message["runtime_intent"] = runtime_intent
    return message


def _repair(
    state: RuntimeGraphState,
    step: LLMCompletionStep,
    instruction: str,
) -> ModelStepResult:
    return ModelStepResult(
        intent="text",
        assistant_message=_assistant_message(state, step),
        repair_instruction=instruction,
    )


def _parse_step(
    state: RuntimeGraphState,
    step: LLMCompletionStep,
    *,
    allowed_tool_names: frozenset[str],
) -> ModelStepResult:
    if step.retry_instruction:
        return _repair(state, step, step.retry_instruction)
    if not step.tool_calls:
        return ModelStepResult(
            intent="text",
            assistant_message=_assistant_message(state, step),
        )

    calls = [cast(JsonObject, deepcopy(call)) for call in step.tool_calls]
    finish = find_finish_call(cast(list[dict], calls))
    wait_calls = [call for call in calls if _tool_name(call) == _RUNTIME_WAIT_TOOL_NAME]
    if finish is not None:
        if len(calls) != 1:
            return _repair(
                state,
                step,
                "`finish` must be the only tool call in the response. Retry without mixing intents.",
            )
        if not finish.valid:
            return _repair(
                state,
                step,
                finish.error or "Retry `finish` with valid content.",
            )
        return ModelStepResult(
            intent="finish",
            assistant_message=_assistant_message(
                state,
                replace(step, content=finish.content),
                runtime_intent="finish",
            ),
            finish_content=finish.content,
        )

    if wait_calls:
        if len(calls) != 1:
            return _repair(
                state,
                step,
                "`wait` must be the only tool call in the response. Retry without mixing intents.",
            )
        function = wait_calls[0].get("function")
        raw_arguments = function.get("arguments") if isinstance(function, Mapping) else None
        try:
            arguments = parse_tool_arguments(raw_arguments)
        except (TypeError, ValueError, json.JSONDecodeError):
            arguments = {}
        waiting_type = arguments.get("waiting_type")
        reason = arguments.get("reason")
        if waiting_type not in {"user", "agent", "external"} or not isinstance(reason, str) or not reason.strip():
            return _repair(
                state,
                step,
                "`wait` requires waiting_type=user|agent|external and a non-empty reason.",
            )
        correlation_id = str(
            uuid.uuid5(
                uuid.UUID(state["registry"].run_id),
                f"model-step:{state['lifecycle'].get('model_step_count', 0) + 1}:wait",
            )
        )
        return ModelStepResult(
            intent="wait",
            assistant_message=_assistant_message(
                state,
                step,
                runtime_intent="wait",
            ),
            waiting_request={
                "waiting_type": waiting_type,
                "correlation_id": correlation_id,
                "reason": reason.strip(),
                "question": (arguments.get("question") if isinstance(arguments.get("question"), str) else None),
            },
        )

    invalid_calls = [
        call
        for call in calls
        if not isinstance(call.get("id"), str)
        or not cast(str, call.get("id")).strip()
        or _tool_name(call) not in allowed_tool_names
    ]
    if invalid_calls:
        return _repair(
            state,
            step,
            "Use only enabled tools and provide a non-empty tool call ID.",
        )
    return ModelStepResult(
        intent="tool_calls",
        assistant_message=_assistant_message(state, step, tool_calls=calls),
        tool_calls=tuple(calls),
    )


class RuntimeModelStepService:
    """Load pinned inputs, enforce budget, and perform one business-model call."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        context_builder: ContextBuilder,
        completion: CompletionPort = complete_llm_once,
        tool_provider: ToolProvider = get_agent_tools_for_llm,
        prompt_builder: PromptBuilder = build_agent_context,
    ) -> None:
        self._session_factory = session_factory
        self._context_builder = context_builder
        self._completion = completion
        self._tool_provider = tool_provider
        self._prompt_builder = prompt_builder

    async def _load(
        self,
        state: RuntimeGraphState,
    ) -> tuple[LLMModel, Agent, dict[str, JsonObject]]:
        registry = state["registry"]
        try:
            tenant_id = uuid.UUID(registry.tenant_id)
            model_id = uuid.UUID(registry.model_id)
            agent_id = uuid.UUID(registry.agent_id or "")
            run_id = uuid.UUID(registry.run_id)
        except ValueError as exc:
            raise ContextBuildError(
                "invalid_runtime_identity",
                "Runtime registry contains an invalid UUID",
            ) from exc
        async with self._session_factory() as db:
            model_result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
            model = model_result.scalar_one_or_none()
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                )
            )
            agent = agent_result.scalar_one_or_none()
            ledger_result = await db.execute(
                select(AgentToolExecution).where(
                    AgentToolExecution.tenant_id == tenant_id,
                    AgentToolExecution.run_id == run_id,
                )
            )
            executions = list(ledger_result.scalars().all())
        if (
            model is None
            or not model.enabled
            or model.tenant_id
            not in {
                None,
                tenant_id,
            }
        ):
            raise ContextBuildError(
                "model_unavailable",
                "pinned Runtime model is disabled or outside the tenant scope",
            )
        if agent is None or agent.status not in _ACTIVE_AGENT_STATUSES or agent.is_expired:
            raise ContextBuildError(
                "agent_unavailable",
                "Runtime Agent is unavailable in the requested tenant",
            )
        return model, agent, _ledger(executions)

    async def complete_once(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> ModelStepResult:
        del context
        try:
            model, agent, ledger = await self._load(state)
            tools = _with_runtime_tools(await self._tool_provider(agent.id))
            initial_build = await self._context_builder.build(
                state,
                tool_execution_ledger=ledger,
            )
            static_prompt, dynamic_prompt = await self._prompt_builder(
                agent.id,
                agent.name,
                agent.role_description or "",
            )
            fixed_prompt_tokens = _estimate_tokens(
                {
                    "static": static_prompt,
                    "dynamic": dynamic_prompt,
                    "runtime": _runtime_sections(initial_build),
                    "recent_session": initial_build.recent_session_messages_snapshot,
                }
            )
            requested_output = get_max_tokens(
                model.provider,
                model.model,
                model.max_output_tokens,
            )
            budget = ModelCapabilityResolver.runtime_budget(
                model,
                requested_max_output_tokens=requested_output,
                static_prompt_tokens=fixed_prompt_tokens,
                tool_schema_tokens=_estimate_tokens(tools),
                reserved_runtime_tokens=256,
                safety_margin_tokens=256,
            )
            build = await self._context_builder.build(
                state,
                tool_execution_ledger=ledger,
                run_message_token_budget=budget.effective_runtime_budget,
                token_counter=_message_token_counter,
            )
            if build.requires_confirmation:
                return ModelStepResult(
                    intent="wait",
                    waiting_request={
                        "waiting_type": "user",
                        "correlation_id": f"tool-confirm:{state['registry'].run_id}",
                        "reason": "A prior tool outcome is unknown and requires confirmation.",
                    },
                )
            if build.blocked:
                return ModelStepResult(
                    intent="wait",
                    waiting_request={
                        "waiting_type": "external",
                        "correlation_id": f"tool-reconcile:{state['registry'].run_id}",
                        "reason": "Tool execution reconciliation is required.",
                    },
                )
            messages = _prompt_messages(
                static_prompt=static_prompt,
                dynamic_prompt=dynamic_prompt,
                build=build,
            )
            step = await self._completion(
                model,
                messages,
                tools=tools,
                agent_id=agent.id,
                supports_vision=bool(model.supports_vision),
            )
            allowed_names = frozenset(name for name in (_tool_name(tool) for tool in tools) if name)
            return _parse_step(
                state,
                step,
                allowed_tool_names=allowed_names,
            )
        except (ContextBuildError, ModelCapabilityError) as exc:
            return _error(exc.code, str(exc))
        except Exception as exc:
            return _error(
                "model_call_failed",
                f"Runtime model step failed: {type(exc).__name__}",
            )


__all__ = ["RuntimeModelStepService"]
