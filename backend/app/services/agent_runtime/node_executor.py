"""Deterministic Runtime node transitions around injected model and tool services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast
import uuid

from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeLifecycle,
    RuntimeNodeName,
    RuntimeStateUpdate,
)
from app.services.llm.finish import FINISH_PROTOCOL_REMINDER


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_WAITING_STATUSES = frozenset({"waiting_user", "waiting_external", "waiting_agent"})
_MAX_APPLIED_COMMAND_IDS = 64

ModelIntent = Literal["tool_calls", "wait", "finish", "text", "error"]
VerificationOutcome = Literal["pass", "repair", "fail"]


class RuntimeNodeTransitionError(RuntimeError):
    """An injected service returned an invalid deterministic transition."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CancelSignal:
    """A durable cancel command observed by the active thread owner."""

    command_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModelStepResult:
    """One schema-validated business-model response."""

    intent: ModelIntent
    assistant_message: JsonObject | None = None
    tool_calls: tuple[JsonObject, ...] = ()
    waiting_request: JsonObject | None = None
    finish_content: str | None = None
    repair_instruction: str | None = None
    error: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ToolStepResult:
    """One sequential, receipt-backed tool batch outcome."""

    messages: tuple[JsonObject, ...] = ()
    waiting_request: JsonObject | None = None
    error: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Deterministic verification outcome for a finish candidate."""

    outcome: VerificationOutcome
    details: JsonObject = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    """Serializable terminal artifacts written into the checkpoint."""

    result_summary: JsonObject
    session_context_delta: JsonObject | None = None
    delivery_request: JsonObject | None = None


class RuntimeCancelSource(Protocol):
    """Read a durable cancel without deriving it from a product projection."""

    async def get_cancel(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> CancelSignal | None: ...


class RuntimeModelStepService(Protocol):
    """Call the pinned business model exactly once."""

    async def complete_once(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> ModelStepResult: ...


class RuntimeToolStepService(Protocol):
    """Execute one pending tool batch through the Tool Execution Ledger."""

    async def execute_pending(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        tool_calls: tuple[JsonObject, ...],
    ) -> ToolStepResult: ...


class RuntimeVerifier(Protocol):
    """Verify a finish candidate without changing product projections."""

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult: ...


class RuntimeFinalizer(Protocol):
    """Build serializable summary, Session delta, and delivery request."""

    async def finalize(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        answer: str,
        verification: VerificationResult,
    ) -> FinalizationResult: ...


class RuntimeTerminalHandler(Protocol):
    """Apply idempotent product side effects after terminal state is checkpointed."""

    async def handle_terminal(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> None: ...


class DeterministicRuntimeVerifier:
    """The v1 fallback verifier when no task-specific verifier is registered."""

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult:
        del context
        if not candidate.strip():
            return VerificationResult(
                outcome="repair",
                reason="finish content is empty",
                details={"code": "empty_finish"},
            )
        if state["lifecycle"].get("pending_tool_calls"):
            return VerificationResult(
                outcome="repair",
                reason="pending tool calls remain",
                details={"code": "pending_tools"},
            )
        return VerificationResult(
            outcome="pass",
            details={"code": "deterministic_checks_passed"},
        )


class DefaultRuntimeFinalizer:
    """Create a conservative terminal summary from the verified answer."""

    async def finalize(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        answer: str,
        verification: VerificationResult,
    ) -> FinalizationResult:
        del state, context
        return FinalizationResult(
            result_summary={
                "summary": answer,
                "verification": dict(verification.details),
                "artifact_refs": [],
            }
        )


def _counter(lifecycle: RuntimeLifecycle, field_name: str) -> int:
    value = lifecycle.get(field_name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeNodeTransitionError(
            "invalid_runtime_counter",
            f"checkpoint {field_name} must be a non-negative integer",
        )
    return value


def _messages(lifecycle: RuntimeLifecycle) -> list[JsonObject]:
    value = lifecycle.get("run_messages", [])
    if not isinstance(value, list) or any(not isinstance(message, Mapping) for message in value):
        raise RuntimeNodeTransitionError(
            "invalid_run_messages",
            "checkpoint run_messages must be an array of objects",
        )
    return [dict(cast(Mapping[str, JsonValue], message)) for message in value]


def _tool_calls(lifecycle: RuntimeLifecycle) -> tuple[JsonObject, ...]:
    value = lifecycle.get("pending_tool_calls", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RuntimeNodeTransitionError(
            "invalid_pending_tool_calls",
            "checkpoint pending_tool_calls must be an array",
        )
    if any(not isinstance(call, Mapping) for call in value):
        raise RuntimeNodeTransitionError(
            "invalid_pending_tool_calls",
            "each pending tool call must be an object",
        )
    return tuple(dict(cast(Mapping[str, JsonValue], call)) for call in value)


def _append_command_id(lifecycle: RuntimeLifecycle, command_id: str) -> list[str]:
    values = lifecycle.get("last_applied_command_ids", [])
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise RuntimeNodeTransitionError(
            "invalid_checkpoint_command_ids",
            "checkpoint command IDs must be non-empty strings",
        )
    return [
        *[value for value in values if value != command_id],
        command_id,
    ][-_MAX_APPLIED_COMMAND_IDS:]


def _error(code: str, message: str) -> JsonObject:
    return {"code": code, "message": message}


def _runtime_message_id(state: RuntimeGraphState, position: str) -> str:
    return str(uuid.uuid5(uuid.UUID(state["registry"].run_id), position))


def _validate_waiting_request(request: JsonObject | None) -> JsonObject:
    if request is None:
        raise RuntimeNodeTransitionError(
            "invalid_waiting_request",
            "wait intent requires a waiting request",
        )
    waiting_type = request.get("waiting_type")
    correlation_id = request.get("correlation_id")
    if waiting_type not in {"user", "agent", "external"}:
        raise RuntimeNodeTransitionError(
            "invalid_waiting_request",
            "waiting_type must be user, agent, or external",
        )
    if not isinstance(correlation_id, str) or not correlation_id:
        raise RuntimeNodeTransitionError(
            "invalid_waiting_request",
            "waiting request requires a non-empty correlation_id",
        )
    return dict(request)


class DeterministicRuntimeNodeExecutor:
    """Own lifecycle transitions while delegating model, tools, and delivery."""

    def __init__(
        self,
        *,
        cancel_source: RuntimeCancelSource,
        model_service: RuntimeModelStepService,
        tool_service: RuntimeToolStepService,
        terminal_handler: RuntimeTerminalHandler,
        verifier: RuntimeVerifier | None = None,
        finalizer: RuntimeFinalizer | None = None,
        max_model_steps: int = 50,
        max_verification_repairs: int = 2,
    ) -> None:
        if max_model_steps <= 0 or max_verification_repairs < 0:
            raise ValueError("Runtime step limits are invalid")
        self._cancel_source = cancel_source
        self._model_service = model_service
        self._tool_service = tool_service
        self._terminal_handler = terminal_handler
        self._verifier = verifier or DeterministicRuntimeVerifier()
        self._finalizer = finalizer or DefaultRuntimeFinalizer()
        self._max_model_steps = max_model_steps
        self._max_verification_repairs = max_verification_repairs

    async def _control_guard(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        if lifecycle["status"] in _TERMINAL_STATUSES:
            lifecycle["next_route"] = "terminal"
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

        cancel = await self._cancel_source.get_cancel(state, context)
        if cancel is not None:
            if not cancel.command_id:
                raise RuntimeNodeTransitionError(
                    "invalid_cancel_command",
                    "cancel command ID must not be blank",
                )
            lifecycle.update(
                {
                    "status": "cancelled",
                    "next_route": "terminal",
                    "reason": cancel.reason or "cancelled_by_command",
                    "last_applied_command_ids": _append_command_id(state["lifecycle"], cancel.command_id),
                    "waiting_request": None,
                    "pending_tool_calls": [],
                }
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _model(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        step_count = _counter(state["lifecycle"], "model_step_count") + 1
        if step_count > self._max_model_steps:
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": "model_step_limit_reached",
                    "error": _error(
                        "model_step_limit_reached",
                        "The Runtime model step limit was reached.",
                    ),
                }
            )
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

        result = await self._model_service.complete_once(state, context)
        lifecycle["model_step_count"] = step_count
        messages = _messages(state["lifecycle"])
        if result.assistant_message is not None:
            messages.append(dict(result.assistant_message))
        lifecycle["run_messages"] = messages

        if result.intent == "tool_calls":
            if not result.tool_calls:
                raise RuntimeNodeTransitionError(
                    "invalid_model_intent",
                    "tool_calls intent requires at least one call",
                )
            lifecycle.update(
                {
                    "status": "running",
                    "next_route": "tool",
                    "pending_tool_calls": [dict(call) for call in result.tool_calls],
                }
            )
        elif result.intent == "wait":
            request = _validate_waiting_request(result.waiting_request)
            waiting_type = cast(str, request["waiting_type"])
            lifecycle.update(
                {
                    "status": f"waiting_{waiting_type}",
                    "next_route": "wait",
                    "waiting_request": request,
                    "pending_tool_calls": [],
                }
            )
        elif result.intent == "finish":
            if not isinstance(result.finish_content, str) or not result.finish_content.strip():
                raise RuntimeNodeTransitionError(
                    "invalid_model_intent",
                    "finish intent requires non-empty content",
                )
            lifecycle.update(
                {
                    "status": "verifying",
                    "next_route": "verify",
                    "final_answer": result.finish_content,
                    "pending_tool_calls": [],
                }
            )
        elif result.intent == "text":
            messages.append(
                {
                    "id": _runtime_message_id(
                        state,
                        f"model-step:{step_count}:repair",
                    ),
                    "role": "user",
                    "content": result.repair_instruction or FINISH_PROTOCOL_REMINDER,
                }
            )
            lifecycle.update(
                {
                    "status": "running",
                    "next_route": "model",
                    "run_messages": messages,
                    "pending_tool_calls": [],
                }
            )
        elif result.intent == "error":
            error = result.error or _error("model_call_failed", "The model call failed.")
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": "model_call_failed",
                    "error": dict(error),
                }
            )
        else:
            raise RuntimeNodeTransitionError(
                "invalid_model_intent",
                f"unsupported model intent {result.intent!r}",
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _tool(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        calls = _tool_calls(state["lifecycle"])
        if not calls:
            raise RuntimeNodeTransitionError(
                "missing_pending_tool_calls",
                "tool route requires pending tool calls",
            )
        result = await self._tool_service.execute_pending(
            state,
            context,
            calls,
        )
        lifecycle = dict(state["lifecycle"])
        messages = _messages(state["lifecycle"])
        messages.extend(dict(message) for message in result.messages)
        lifecycle.update(
            {
                "run_messages": messages,
                "pending_tool_calls": [],
            }
        )
        if result.waiting_request is not None:
            request = _validate_waiting_request(result.waiting_request)
            waiting_type = cast(str, request["waiting_type"])
            lifecycle.update(
                {
                    "status": f"waiting_{waiting_type}",
                    "next_route": "wait",
                    "waiting_request": request,
                    "error": dict(result.error) if result.error is not None else None,
                }
            )
        elif result.error is not None:
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": "tool_execution_failed",
                    "error": dict(result.error),
                }
            )
        else:
            lifecycle.update(
                {
                    "status": "running",
                    "next_route": "model",
                    "waiting_request": None,
                    "error": None,
                }
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        candidate = state["lifecycle"].get("final_answer")
        if not isinstance(candidate, str):
            raise RuntimeNodeTransitionError(
                "missing_finish_candidate",
                "verify requires a finish candidate",
            )
        verification = await self._verifier.verify(state, context, candidate)
        lifecycle = dict(state["lifecycle"])
        lifecycle["verification_result"] = {
            "outcome": verification.outcome,
            "reason": verification.reason,
            "details": dict(verification.details),
        }
        if verification.outcome == "pass":
            finalized = await self._finalizer.finalize(
                state,
                context,
                candidate,
                verification,
            )
            lifecycle.update(
                {
                    "status": "completed",
                    "next_route": "terminal",
                    "result_summary": dict(finalized.result_summary),
                    "session_context_delta": (
                        dict(finalized.session_context_delta) if finalized.session_context_delta is not None else None
                    ),
                    "delivery_request": (
                        dict(finalized.delivery_request) if finalized.delivery_request is not None else None
                    ),
                }
            )
        elif verification.outcome == "repair":
            attempts = _counter(state["lifecycle"], "verification_attempt_count") + 1
            lifecycle["verification_attempt_count"] = attempts
            if attempts > self._max_verification_repairs:
                lifecycle.update(
                    {
                        "status": "failed",
                        "next_route": "terminal",
                        "reason": "verification_repair_limit_reached",
                        "error": _error(
                            "verification_repair_limit_reached",
                            "The finish candidate did not pass verification.",
                        ),
                    }
                )
            else:
                messages = _messages(state["lifecycle"])
                messages.append(
                    {
                        "id": _runtime_message_id(
                            state,
                            f"verification:{attempts}:repair",
                        ),
                        "role": "user",
                        "content": verification.reason or "The finish candidate needs repair before completion.",
                    }
                )
                lifecycle.update(
                    {
                        "status": "running",
                        "next_route": "model",
                        "run_messages": messages,
                        "final_answer": None,
                    }
                )
        elif verification.outcome == "fail":
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": verification.reason or "verification_failed",
                    "error": _error(
                        "verification_failed",
                        verification.reason or "Runtime verification failed.",
                    ),
                }
            )
        else:
            raise RuntimeNodeTransitionError(
                "invalid_verification_outcome",
                f"unsupported verification outcome {verification.outcome!r}",
            )
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _wait(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        resume_value: JsonValue | None,
    ) -> RuntimeStateUpdate:
        if state["lifecycle"]["status"] not in _WAITING_STATUSES:
            raise RuntimeNodeTransitionError(
                "run_not_waiting",
                "wait node requires a waiting lifecycle",
            )
        if not isinstance(resume_value, Mapping):
            raise RuntimeNodeTransitionError(
                "invalid_resume_payload",
                "resume value must be an object",
            )
        lifecycle = dict(state["lifecycle"])
        messages = _messages(state["lifecycle"])
        messages.append(
            {
                "id": _runtime_message_id(
                    state,
                    f"resume:{context.command_id}",
                ),
                "role": "user",
                "content": dict(cast(Mapping[str, JsonValue], resume_value)),
                "runtime_input": "resume",
            }
        )
        lifecycle.update(
            {
                "status": "running",
                "next_route": "model",
                "reason": None,
                "waiting_request": None,
                "run_messages": messages,
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
        if node == "control_guard":
            return await self._control_guard(state, context)
        if node == "model":
            return await self._model(state, context)
        if node == "tool":
            return await self._tool(state, context)
        if node == "verify":
            return await self._verify(state, context)
        if node == "wait":
            return await self._wait(state, context, resume_value)
        if node == "terminal":
            if state["lifecycle"]["status"] not in _TERMINAL_STATUSES:
                raise RuntimeNodeTransitionError(
                    "run_not_terminal",
                    "terminal handler requires a terminal lifecycle",
                )
            await self._terminal_handler.handle_terminal(state, context)
            return {"lifecycle": dict(state["lifecycle"])}
        raise RuntimeNodeTransitionError(
            "unsupported_runtime_node",
            f"unsupported Runtime node {node!r}",
        )


__all__ = [
    "CancelSignal",
    "DefaultRuntimeFinalizer",
    "DeterministicRuntimeNodeExecutor",
    "DeterministicRuntimeVerifier",
    "FinalizationResult",
    "ModelStepResult",
    "RuntimeCancelSource",
    "RuntimeFinalizer",
    "RuntimeModelStepService",
    "RuntimeNodeTransitionError",
    "RuntimeTerminalHandler",
    "RuntimeToolStepService",
    "RuntimeVerifier",
    "ToolStepResult",
    "VerificationResult",
]
