"""Deterministic Runtime node transitions around injected model and tool services."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeLifecycle,
    RuntimeNodeName,
    RuntimeStateUpdate,
    runtime_messages_as_json,
)
from app.services.agent_runtime.tool_repair_budget import (
    ToolRepairBudgetError,
    apply_tool_result,
    reset_tool_repair_episodes,
)
from app.services.llm.caller import (
    WRITE_FILE_PROTOCOL_FAILURE_MESSAGE,
    WRITE_FILE_PROTOCOL_REPAIR_COUNTER_KEY,
    WRITE_FILE_PROTOCOL_REPAIR_LIMIT,
)
from app.services.llm.multimodal_content import parse_multimodal_content

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_WAITING_STATUSES = frozenset({"waiting_user", "waiting_external", "waiting_agent"})

ModelIntent = Literal["tool_calls", "wait", "finish", "text", "error"]
VerificationOutcome = Literal["pass", "repair", "fail"]


class RuntimeNodeTransitionError(RuntimeError):
    """An injected service returned an invalid deterministic transition."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeInvocationCancelled(RuntimeError):
    """Stop an invocation without committing a synthetic cancelled checkpoint."""

    def __init__(self, signal: CancelSignal) -> None:
        super().__init__(signal.reason or "runtime invocation cancelled")
        self.cancel_command_id = signal.command_id
        self.reason = signal.reason


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
    step_tool_context: JsonObject | None = None
    waiting_request: JsonObject | None = None
    finish_content: str | None = None
    finish_mention_participant_ids: tuple[str, ...] = ()
    finish_delivery_intent: JsonObject | None = None
    repair_instruction: str | None = None
    repair_code: str | None = None
    repair_tool_name: str | None = None
    repair_reset_reason: str | None = None
    error: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ToolStepResult:
    """One sequential, receipt-backed tool batch outcome."""

    messages: tuple[JsonObject, ...] = ()
    waiting_request: JsonObject | None = None
    pending_tool_calls: tuple[JsonObject, ...] = ()
    step_tool_context: JsonObject | None = None
    pending_group_at_changed: bool = False
    pending_group_at: JsonObject | None = None
    cancel_signal: CancelSignal | None = None
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


@dataclass(frozen=True, slots=True)
class RunCompactResult:
    """One optional atomic replacement of the Thread's model-visible history."""

    compacted: bool = False
    thread_summary: JsonObject | None = None
    recent_messages: tuple[JsonObject, ...] | None = None
    covered_through_message_id: str | None = None


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


class RuntimeRunCompactor(Protocol):
    """Compact only safely covered Thread messages into checkpoint state."""

    async def compact_if_needed(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RunCompactResult: ...


class NoopRuntimeRunCompactor:
    """Default used by isolated node tests and non-production composition."""

    async def compact_if_needed(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RunCompactResult:
        del state, context
        return RunCompactResult()


class RuntimeToolStepService(Protocol):
    """Execute pending tools through the Tool Execution Ledger."""

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

    @staticmethod
    def _verified_refs(
        verification: VerificationResult,
        field_name: str,
    ) -> list[JsonValue]:
        raw_refs = verification.details.get(field_name, [])
        if not isinstance(raw_refs, list) or any(
            not isinstance(reference, str) or not reference.strip()
            for reference in raw_refs
        ):
            raise RuntimeNodeTransitionError(
                "invalid_verification_result",
                f"verified {field_name} must be a list of non-empty strings",
            )
        return list(dict.fromkeys(reference.strip() for reference in raw_refs))

    async def finalize(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        answer: str,
        verification: VerificationResult,
    ) -> FinalizationResult:
        del state
        source_run_id = context.run_id
        artifact_refs = self._verified_refs(verification, "artifact_refs")
        evidence_refs = self._verified_refs(verification, "evidence_refs")
        return FinalizationResult(
            result_summary={
                "summary": answer,
                "verification": dict(verification.details),
                "artifact_refs": artifact_refs,
                "evidence_refs": evidence_refs,
            },
            session_context_delta={
                "source_run_id": source_run_id,
                "new_requirements": [],
                "new_decisions": [],
                "resolved_open_items": [],
                "new_open_items": [],
                "evidence_refs": evidence_refs,
                "workspace_refs": [],
                "result_summary": answer,
            },
        )


def _counter(lifecycle: RuntimeLifecycle, field_name: str) -> int:
    value = lifecycle.get(field_name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeNodeTransitionError(
            "invalid_runtime_counter",
            f"checkpoint {field_name} must be a non-negative integer",
        )
    return value


def _model_protocol_repairs(lifecycle: RuntimeLifecycle) -> dict[str, int]:
    raw = lifecycle.get("model_protocol_repairs", {})
    if not isinstance(raw, Mapping):
        raise RuntimeNodeTransitionError(
            "invalid_model_protocol_repairs",
            "checkpoint model_protocol_repairs must be an object",
        )
    repairs: dict[str, int] = {}
    for code, count in raw.items():
        if (
            not isinstance(code, str)
            or not code
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise RuntimeNodeTransitionError(
                "invalid_model_protocol_repairs",
                "checkpoint model protocol repair entries must be non-negative integers",
            )
        repairs[code] = count
    return repairs


def _messages(state: RuntimeGraphState) -> list[JsonObject]:
    try:
        value = runtime_messages_as_json(state)
    except (TypeError, ValueError) as exc:
        raise RuntimeNodeTransitionError(
            "invalid_thread_messages",
            "checkpoint messages must use the LangGraph messages channel",
        ) from exc
    return [dict(message) for message in value]


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


def _tool_call_name(call: Mapping[str, object]) -> str:
    function = call.get("function")
    name = function.get("name") if isinstance(function, Mapping) else call.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else "unknown_tool"


def _paused_tail_result(
    context: RuntimeContext,
    call: Mapping[str, object],
) -> JsonObject:
    call_id = str(call.get("id") or "")
    return {
        "id": _runtime_message_id(context, f"tool-repair-paused:{call_id}"),
        "role": "tool",
        "tool_call_id": call_id,
        "name": _tool_call_name(call),
        "content": "Tool execution was skipped because the repair episode paused.",
        "execution_status": "failed",
        "error_code": "tool_batch_paused",
        "model_action": "ask_user",
        "side_effect_state": "none",
        "safe_remediation": "Wait for corrected user input before proposing Tools again.",
    }


def _verification_fingerprint(verification: VerificationResult) -> str:
    payload = json.dumps(
        {
            "code": verification.details.get("code"),
            "reason": verification.reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _verification_repair_attempt(
    lifecycle: RuntimeLifecycle,
    verification: VerificationResult,
) -> tuple[int, JsonObject]:
    fingerprint = _verification_fingerprint(verification)
    raw = lifecycle.get("verification_repair_episode")
    if raw is not None and not isinstance(raw, Mapping):
        raise RuntimeNodeTransitionError(
            "invalid_verification_repair_episode",
            "checkpoint verification repair episode must be an object",
        )
    prior_fingerprint = raw.get("fingerprint") if isinstance(raw, Mapping) else None
    prior_attempts = raw.get("attempts", 0) if isinstance(raw, Mapping) else 0
    if (
        isinstance(prior_attempts, bool)
        or not isinstance(prior_attempts, int)
        or prior_attempts < 0
    ):
        raise RuntimeNodeTransitionError(
            "invalid_verification_repair_episode",
            "checkpoint verification repair attempts must be non-negative",
        )
    attempts = prior_attempts + 1 if prior_fingerprint == fingerprint else 1
    return attempts, {
        "fingerprint": fingerprint,
        "attempts": attempts,
        "issue_code": verification.details.get("code"),
    }


def _error(code: str, message: str) -> JsonObject:
    return {"code": code, "message": message}


def _message_for_channel(message: JsonObject) -> JsonObject:
    """Normalize harness dictionaries to LangGraph's standard message input."""
    normalized = dict(message)
    role = normalized.get("role")
    if role not in {"user", "assistant", "tool", "system"}:
        raise RuntimeNodeTransitionError(
            "invalid_thread_message",
            "Runtime message role is unsupported",
        )
    normalized.setdefault("content", "")
    raw_calls = normalized.get("tool_calls")
    if isinstance(raw_calls, list):
        calls: list[JsonObject] = []
        for raw in raw_calls:
            if not isinstance(raw, Mapping):
                raise RuntimeNodeTransitionError(
                    "invalid_thread_message",
                    "assistant tool calls must be objects",
                )
            call = dict(raw)
            if isinstance(call.get("function"), Mapping):
                calls.append(cast(JsonObject, call))
                continue
            name = call.get("name")
            arguments = call.get("arguments", {})
            if not isinstance(name, str) or not name:
                raise RuntimeNodeTransitionError(
                    "invalid_thread_message",
                    "assistant tool calls require a name",
                )
            calls.append(
                {
                    "id": cast(str, call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": (
                            arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments, ensure_ascii=False)
                        ),
                    },
                }
            )
        normalized["tool_calls"] = calls
    return cast(JsonObject, normalized)


def _resume_message_content(resume_value: Mapping[str, JsonValue]) -> str | list:
    resume_type = resume_value.get("resume_type")
    payload = resume_value.get("payload")
    if resume_type in {"user_input", "tool_reconciliation"} and isinstance(payload, Mapping):
        content = payload.get("content")
        if isinstance(content, (str, list)):
            return parse_multimodal_content(content)
    return json.dumps(
        resume_value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    )


def _resume_confirmation_text(
    resume_value: Mapping[str, JsonValue],
) -> str | None:
    if resume_value.get("resume_type") not in {"user_input", "tool_reconciliation"}:
        return None
    payload = resume_value.get("payload")
    if not isinstance(payload, Mapping):
        return None
    confirmation_text = payload.get("confirmation_text")
    if not isinstance(confirmation_text, str) or not confirmation_text.strip():
        return None
    return confirmation_text.strip()[:500]


def _runtime_message_id(context: RuntimeContext, position: str) -> str:
    return str(uuid.uuid5(uuid.UUID(context.run_id), position))


def _schedule_compact(
    lifecycle: dict,
) -> None:
    lifecycle["next_route"] = "compact"


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


def _async_poll_call_from_resume(resume_value: Mapping[str, object]) -> JsonObject | None:
    """Recover a pre-scheduler async wait from its durable timer command."""
    if resume_value.get("resume_type") != "timer":
        return None
    payload = resume_value.get("payload")
    if not isinstance(payload, Mapping):
        return None
    poll_call_id = payload.get("poll_call_id")
    poll = payload.get("poll")
    if not isinstance(poll_call_id, str) or not poll_call_id:
        return None
    if not isinstance(poll, Mapping):
        return None
    tool_name = poll.get("tool")
    arguments = poll.get("arguments")
    if (
        not isinstance(tool_name, str)
        or not tool_name.strip()
        or not isinstance(arguments, Mapping)
    ):
        return None
    return {
        "id": poll_call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(
                dict(arguments),
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    }


class DeterministicRuntimeNodeExecutor:
    """Own lifecycle transitions while delegating model, tools, and delivery."""

    def __init__(
        self,
        *,
        cancel_source: RuntimeCancelSource,
        model_service: RuntimeModelStepService,
        tool_service: RuntimeToolStepService,
        run_compactor: RuntimeRunCompactor | None = None,
        verifier: RuntimeVerifier | None = None,
        finalizer: RuntimeFinalizer | None = None,
        max_verification_repairs: int = 2,
    ) -> None:
        if max_verification_repairs < 0:
            raise ValueError("Runtime verification repair limit is invalid")
        self._cancel_source = cancel_source
        self._model_service = model_service
        self._tool_service = tool_service
        self._run_compactor = run_compactor or NoopRuntimeRunCompactor()
        self._verifier = verifier or DeterministicRuntimeVerifier()
        self._finalizer = finalizer or DefaultRuntimeFinalizer()
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
            raise RuntimeInvocationCancelled(cancel)
        return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}

    async def _compact(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        if lifecycle.get("status") != "running":
            raise RuntimeNodeTransitionError(
                "invalid_compact_status",
                "Thread Compact may run only immediately before a business model call",
            )
        try:
            result = await self._run_compactor.compact_if_needed(
                state,
                context,
            )
        except Exception as exc:
            if not getattr(exc, "is_deterministic_compact_error", False):
                raise
            code = getattr(exc, "code", "thread_compact_failed")
            safe_code = code if isinstance(code, str) and code else "thread_compact_failed"
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": safe_code,
                    "error": _error(safe_code, str(exc)),
                }
            )
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        update: RuntimeStateUpdate = {}
        if result.compacted:
            if (
                result.thread_summary is None
                or result.recent_messages is None
                or not isinstance(result.covered_through_message_id, str)
                or not result.covered_through_message_id
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_thread_compact_result",
                    "successful Thread Compact requires summary, recent messages, and watermark",
                )
            update.update(
                {
                    "thread_summary": dict(result.thread_summary),
                    "summary_covered_through_message_id": result.covered_through_message_id,
                    "messages": [
                        RemoveMessage(id=REMOVE_ALL_MESSAGES),
                        *[
                            _message_for_channel(dict(message))
                            for message in result.recent_messages
                        ],
                    ],
                }
            )
        lifecycle["next_route"] = "model"
        update["lifecycle"] = cast(RuntimeLifecycle, lifecycle)
        return update

    async def _model(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RuntimeStateUpdate:
        lifecycle = dict(state["lifecycle"])
        step_count = _counter(state["lifecycle"], "model_step_count") + 1
        model_step_limit = context.model_turn_limit
        if (
            isinstance(model_step_limit, bool)
            or not isinstance(model_step_limit, int)
            or model_step_limit <= 0
        ):
            raise RuntimeNodeTransitionError(
                "invalid_model_step_limit",
                "Runtime Context model_turn_limit must be a positive integer",
            )
        if step_count > model_step_limit:
            lifecycle.pop("pending_group_at", None)
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
        if result.repair_reset_reason is not None:
            if result.repair_reset_reason != "explicit_user_correction":
                raise RuntimeNodeTransitionError(
                    "invalid_tool_repair_reset",
                    "model repair reset reason is unsupported",
                )
            lifecycle["tool_repair_reset"] = {
                "reason": result.repair_reset_reason,
                "command_id": context.command_id,
                "consumed_at_model_step": step_count,
            }
        if result.intent != "finish":
            lifecycle.pop("finish_delivery_intent", None)
        new_messages: list[JsonObject] = []
        if result.assistant_message is not None:
            assistant_message = dict(result.assistant_message)
            assistant_message["runtime_run_id"] = context.run_id
            if result.intent == "text":
                assistant_message["runtime_intent"] = "repair_draft"
            new_messages.append(assistant_message)

        if result.intent == "tool_calls":
            if not result.tool_calls:
                raise RuntimeNodeTransitionError(
                    "invalid_model_intent",
                    "tool_calls intent requires at least one call",
                )
            if result.step_tool_context is not None and not isinstance(
                result.step_tool_context,
                Mapping,
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_step_tool_context",
                    "tool_calls intent Step Tool Context must be an object",
                )
            lifecycle.update(
                {
                    "status": "running",
                    "next_route": "tool",
                    "pending_tool_calls": [dict(call) for call in result.tool_calls],
                }
            )
            if result.step_tool_context is not None:
                lifecycle["step_tool_context"] = dict(result.step_tool_context)
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
            finish_delivery_intent = result.finish_delivery_intent
            if finish_delivery_intent is not None and not isinstance(
                finish_delivery_intent,
                Mapping,
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_group_handoff_intent",
                    "finish delivery intent must be an object",
                )
            lifecycle.update(
                {
                    "status": "verifying",
                    "next_route": "verify",
                    "final_answer": result.finish_content,
                    "finish_delivery_intent": (
                        dict(finish_delivery_intent)
                        if finish_delivery_intent is not None
                        else None
                    ),
                    "pending_tool_calls": [],
                }
            )
        elif result.intent == "text":
            repair_code = result.repair_code
            if repair_code is not None:
                if not repair_code:
                    raise RuntimeNodeTransitionError(
                        "invalid_model_repair_code",
                        "model repair_code must not be blank",
                    )
                repairs = _model_protocol_repairs(state["lifecycle"])
                is_write_file_repair = (
                    repair_code == "invalid_tool_call"
                    and result.repair_tool_name == "write_file"
                )
                repair_limit = (
                    WRITE_FILE_PROTOCOL_REPAIR_LIMIT
                    if is_write_file_repair
                    else 10
                    if repair_code == "invalid_tool_call"
                    else 1
                )
                repair_counter_key = (
                    WRITE_FILE_PROTOCOL_REPAIR_COUNTER_KEY
                    if is_write_file_repair
                    else repair_code
                )
                if repairs.get(repair_counter_key, 0) >= repair_limit:
                    violation_code = {
                        "empty_output": "model_empty_output",
                        "incomplete_output": "model_incomplete_output",
                        "missing_finish": "finish_protocol_violation",
                    }.get(repair_code, "model_tool_protocol_violation")
                    if is_write_file_repair:
                        error_message = WRITE_FILE_PROTOCOL_FAILURE_MESSAGE
                    elif repair_code == "incomplete_output":
                        error_message = (
                            "The model output remained truncated after one bounded repair."
                        )
                    elif repair_code == "empty_output":
                        error_message = (
                            "The model repeated an empty final response after one bounded repair."
                        )
                    else:
                        error_message = (
                            f"The model repeated the {repair_code!r} protocol error "
                            f"after {repair_limit} bounded repair attempt(s). "
                            "Native tool calling is not working for this Run."
                        )
                    lifecycle.pop("pending_group_at", None)
                    lifecycle.update(
                        {
                            "status": "failed",
                            "next_route": "terminal",
                            "reason": violation_code,
                            "pending_tool_calls": [],
                            "error": _error(
                                violation_code,
                                error_message,
                            ),
                        }
                    )
                else:
                    repairs[repair_counter_key] = (
                        repairs.get(repair_counter_key, 0) + 1
                    )
                    new_messages.append(
                        {
                            "id": _runtime_message_id(
                                context,
                                f"model-step:{step_count}:repair",
                            ),
                            "role": "user",
                            "content": (
                                result.repair_instruction
                                or "Return one complete, non-empty final response."
                            ),
                            "runtime_intent": "repair",
                            "runtime_run_id": context.run_id,
                        }
                    )
                    lifecycle.update(
                        {
                            "status": "running",
                            "model_protocol_repairs": cast(JsonObject, repairs),
                            "pending_tool_calls": [],
                        }
                    )
                    _schedule_compact(lifecycle)
            else:
                new_messages.append(
                    {
                        "id": _runtime_message_id(
                            context,
                            f"model-step:{step_count}:repair",
                        ),
                        "role": "user",
                        "content": (
                            result.repair_instruction
                            or "Retry after resolving the reported business constraint."
                        ),
                        "runtime_intent": "repair",
                        "runtime_run_id": context.run_id,
                    }
                )
                lifecycle.update(
                    {
                        "status": "running",
                        "pending_tool_calls": [],
                    }
                )
                _schedule_compact(lifecycle)
        elif result.intent == "error":
            error = result.error or _error("model_call_failed", "The model call failed.")
            error_code = error.get("code")
            reason = (
                error_code
                if isinstance(error_code, str) and error_code
                else "model_call_failed"
            )
            lifecycle.pop("pending_group_at", None)
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": reason,
                    "error": dict(error),
                }
            )
        else:
            raise RuntimeNodeTransitionError(
                "invalid_model_intent",
                f"unsupported model intent {result.intent!r}",
            )
        update: RuntimeStateUpdate = {
            "lifecycle": cast(RuntimeLifecycle, lifecycle),
        }
        if new_messages:
            update["messages"] = [
                _message_for_channel(message) for message in new_messages
            ]
        return update

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
        call_ids = [call.get("id") for call in calls]
        if (
            any(not isinstance(call_id, str) or not call_id.strip() for call_id in call_ids)
            or len(set(call_ids)) != len(call_ids)
        ):
            lifecycle = dict(state["lifecycle"])
            lifecycle.pop("pending_group_at", None)
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": "tool_execution_failed",
                    "error": _error(
                        "invalid_tool_call",
                        "pending tool calls require unique non-empty IDs",
                    ),
                }
            )
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        # One LangGraph Tool node task owns one receipt. RetryPolicy budgets are
        # node-task scoped, so passing the whole batch here would make several
        # receipts share one retry counter.
        current_call = calls[0]
        tail_calls = calls[1:]
        result = await self._tool_service.execute_pending(
            state,
            context,
            (current_call,),
        )
        resumed_waiting_request = state["lifecycle"].get(
            "resumed_waiting_request"
        )
        discard_tail_calls = (
            isinstance(resumed_waiting_request, Mapping)
            and resumed_waiting_request.get(
                "discard_remaining_tool_calls_on_resume"
            )
            is True
            and resumed_waiting_request.get("tool_call_id")
            == current_call.get("id")
        )
        pending_calls = (
            tuple(result.pending_tool_calls)
            if discard_tail_calls
            else (*result.pending_tool_calls, *tail_calls)
        )
        lifecycle = dict(state["lifecycle"])
        repair_pause_reason: str | None = None
        repair_pause_tool: str | None = None
        try:
            repair_episodes: object = lifecycle.get("tool_repair_episodes")
            for message in result.messages:
                transition = apply_tool_result(
                    repair_episodes,
                    message,
                    model_step=_counter(state["lifecycle"], "model_step_count"),
                )
                repair_episodes = transition.episodes
                if transition.pause_reason is not None:
                    repair_pause_reason = transition.pause_reason
                    repair_pause_tool = transition.paused_tool_name
            lifecycle["tool_repair_episodes"] = cast(JsonObject, repair_episodes)
        except ToolRepairBudgetError as exc:
            raise RuntimeNodeTransitionError(
                "invalid_tool_repair_episodes",
                str(exc),
            ) from exc
        lifecycle.pop("resumed_waiting_request", None)
        lifecycle.update(
            {
                "pending_tool_calls": [dict(call) for call in pending_calls],
            }
        )
        if result.step_tool_context is not None:
            if not isinstance(result.step_tool_context, Mapping):
                raise RuntimeNodeTransitionError(
                    "invalid_step_tool_context",
                    "Tool Step context update must be an object",
                )
            lifecycle["step_tool_context"] = dict(result.step_tool_context)
        if result.pending_group_at_changed:
            if result.pending_group_at is None:
                lifecycle.pop("pending_group_at", None)
            elif not isinstance(result.pending_group_at, Mapping):
                raise RuntimeNodeTransitionError(
                    "invalid_pending_group_at",
                    "tool result pending_group_at must be an object",
                )
            else:
                lifecycle["pending_group_at"] = dict(result.pending_group_at)
        if result.cancel_signal is not None:
            cancel = result.cancel_signal
            if not cancel.command_id:
                raise RuntimeNodeTransitionError(
                    "invalid_cancel_command",
                    "cancel command ID must not be blank",
                )
            raise RuntimeInvocationCancelled(cancel)
        elif result.waiting_request is not None:
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
            lifecycle.pop("pending_group_at", None)
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": "tool_execution_failed",
                    "error": dict(result.error),
                }
            )
        elif repair_pause_reason is not None:
            lifecycle.pop("step_tool_context", None)
            lifecycle.update(
                {
                    "status": "failed",
                    "next_route": "terminal",
                    "reason": repair_pause_reason,
                    "pending_tool_calls": [],
                    "waiting_request": None,
                    "error": _error(
                        repair_pause_reason,
                        f"Tool {repair_pause_tool or 'unknown'} reached its "
                        "repair safety limit.",
                    ),
                }
            )
        else:
            lifecycle.update(
                {
                    "status": "running",
                    "waiting_request": None,
                    "error": None,
                }
            )
            if pending_calls:
                lifecycle["next_route"] = "tool"
            else:
                lifecycle.pop("step_tool_context", None)
                _schedule_compact(lifecycle)
        update: RuntimeStateUpdate = {
            "lifecycle": cast(RuntimeLifecycle, lifecycle),
        }
        output_messages = [
            _message_for_channel(dict(message)) for message in result.messages
        ]
        if repair_pause_reason is not None:
            output_messages.extend(
                _message_for_channel(_paused_tail_result(context, call))
                for call in tail_calls
            )
        if (
            result.cancel_signal is None
            and result.waiting_request is None
            and result.error is None
            and not pending_calls
        ):
            deferred_resume_messages = lifecycle.get(
                "deferred_resume_messages",
                [],
            )
            if not isinstance(deferred_resume_messages, list) or any(
                not isinstance(message, Mapping)
                for message in deferred_resume_messages
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_deferred_resume_messages",
                    "deferred resume messages must be an array of objects",
                )
            output_messages.extend(
                _message_for_channel(dict(message))
                for message in deferred_resume_messages
            )
            lifecycle["deferred_resume_messages"] = []
            update["lifecycle"] = cast(RuntimeLifecycle, lifecycle)
        if output_messages:
            update["messages"] = [
                *output_messages,
            ]
        return update

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
        raw_finish_delivery_intent = lifecycle.get("finish_delivery_intent")
        if raw_finish_delivery_intent is not None and not isinstance(
            raw_finish_delivery_intent,
            Mapping,
        ):
            raise RuntimeNodeTransitionError(
                "invalid_group_handoff_intent",
                "checkpoint finish delivery intent must be an object",
            )
        lifecycle["verification_result"] = {
            "outcome": verification.outcome,
            "reason": verification.reason,
            "details": dict(verification.details),
        }
        if verification.outcome == "pass":
            lifecycle.pop("verification_repair_episode", None)
            lifecycle["verification_attempt_count"] = 0
            finalized = await self._finalizer.finalize(
                state,
                context,
                candidate,
                verification,
            )
            delivery_request = (
                dict(finalized.delivery_request)
                if finalized.delivery_request is not None
                else None
            )
            if raw_finish_delivery_intent is not None:
                delivery_request = delivery_request or {}
                existing_handoff = delivery_request.get("group_handoff")
                if existing_handoff is not None and existing_handoff != dict(
                    raw_finish_delivery_intent
                ):
                    raise RuntimeNodeTransitionError(
                        "invalid_group_handoff_intent",
                        "finalizer changed the frozen Group handoff intent",
                    )
                delivery_request["content"] = candidate
                delivery_request["group_handoff"] = dict(
                    raw_finish_delivery_intent
                )
            lifecycle.pop("finish_delivery_intent", None)
            lifecycle.pop("pending_group_at", None)
            lifecycle.update(
                {
                    "status": "completed",
                    "next_route": "terminal",
                    "result_summary": dict(finalized.result_summary),
                    "session_context_delta": (
                        dict(finalized.session_context_delta) if finalized.session_context_delta is not None else None
                    ),
                    "delivery_request": (
                        delivery_request
                    ),
                }
            )
        elif verification.outcome == "repair":
            if verification.details.get("code") == "task_completion_repair_required":
                attempts = _counter(
                    state["lifecycle"],
                    "verification_attempt_count",
                ) + 1
                verification_episode = {
                    "fingerprint": "task_completion_repair_required",
                    "attempts": attempts,
                    "issue_code": "task_completion_repair_required",
                }
            else:
                attempts, verification_episode = _verification_repair_attempt(
                    state["lifecycle"],
                    verification,
                )
            lifecycle["verification_attempt_count"] = attempts
            lifecycle["verification_repair_episode"] = verification_episode
            if (
                attempts > self._max_verification_repairs
                and verification.details.get("code")
                != "task_completion_repair_required"
            ):
                lifecycle.pop("finish_delivery_intent", None)
                lifecycle.pop("pending_group_at", None)
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
            elif attempts > self._max_verification_repairs:
                exhausted_details = {
                    **dict(verification.details),
                    "code": "completion_gate_exhausted",
                    "repair_attempts": self._max_verification_repairs,
                    "rejected_candidates": attempts,
                    "last_outcome": verification.outcome,
                    "last_reason": verification.reason,
                }
                exhausted = VerificationResult(
                    outcome="pass",
                    details=cast(JsonObject, exhausted_details),
                )
                finalized = await self._finalizer.finalize(
                    state,
                    context,
                    candidate,
                    exhausted,
                )
                delivery_request = (
                    dict(finalized.delivery_request)
                    if finalized.delivery_request is not None
                    else None
                )
                if raw_finish_delivery_intent is not None:
                    delivery_request = delivery_request or {}
                    delivery_request["content"] = candidate
                    delivery_request["group_handoff"] = dict(
                        raw_finish_delivery_intent
                    )
                lifecycle.pop("finish_delivery_intent", None)
                lifecycle.pop("pending_group_at", None)
                lifecycle["verification_result"] = {
                    "outcome": "exhausted",
                    "reason": verification.reason,
                    "details": cast(JsonObject, exhausted_details),
                }
                lifecycle.update(
                    {
                        "status": "completed",
                        "next_route": "terminal",
                        "reason": "completion_gate_exhausted",
                        "result_summary": dict(finalized.result_summary),
                        "session_context_delta": (
                            dict(finalized.session_context_delta)
                            if finalized.session_context_delta is not None
                            else None
                        ),
                        "delivery_request": delivery_request,
                    }
                )
            else:
                lifecycle.pop("finish_delivery_intent", None)
                lifecycle.update(
                    {
                        "status": "running",
                        "final_answer": None,
                    }
                )
                _schedule_compact(lifecycle)
                return {
                    "lifecycle": cast(RuntimeLifecycle, lifecycle),
                    "messages": [
                        _message_for_channel({
                            "id": _runtime_message_id(
                                context,
                                f"verification:{attempts}:repair",
                            ),
                            "role": "user",
                            "content": verification.reason
                            or "The finish candidate needs repair before completion.",
                            "runtime_intent": "repair",
                            "runtime_run_id": context.run_id,
                        })
                    ],
                }
        elif verification.outcome == "fail":
            lifecycle.pop("finish_delivery_intent", None)
            lifecycle.pop("pending_group_at", None)
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
        waiting_status = state["lifecycle"]["status"]
        waiting_request = _validate_waiting_request(
            cast(JsonObject | None, state["lifecycle"].get("waiting_request"))
        )
        lifecycle.update(
            {
                "status": "running",
                "reason": None,
                "waiting_request": None,
            }
        )
        resume_message = _message_for_channel({
            "id": _runtime_message_id(
                context,
                f"resume:{context.command_id}",
            ),
            "role": "user",
            "content": _resume_message_content(
                cast(Mapping[str, JsonValue], resume_value)
            ),
            "runtime_input": "resume",
            "runtime_run_id": context.run_id,
        })
        if (
            waiting_status == "waiting_user"
            and state["lifecycle"].get("reason")
            in {
                "tool_repair_same_fingerprint_limit_reached",
                "tool_repair_episode_limit_reached",
            }
            and resume_value.get("resume_type") == "user_input"
        ):
            try:
                lifecycle["tool_repair_episodes"] = reset_tool_repair_episodes(
                    lifecycle.get("tool_repair_episodes")
                )
            except ToolRepairBudgetError as exc:
                raise RuntimeNodeTransitionError(
                    "invalid_tool_repair_episodes",
                    str(exc),
                ) from exc
            lifecycle["tool_repair_reset"] = {
                "reason": "explicit_user_correction",
                "command_id": context.command_id,
                "at_model_step": _counter(
                    state["lifecycle"],
                    "model_step_count",
                ),
            }
        confirmation_text = _resume_confirmation_text(
            cast(Mapping[str, JsonValue], resume_value)
        )
        if confirmation_text is not None:
            resume_message["runtime_confirmation_text"] = confirmation_text
        if resume_value.get("resume_type") == "tool_reconciliation":
            payload = resume_value.get("payload")
            reconciliation_action = (
                payload.get("workspace_resolution_action")
                if isinstance(payload, Mapping)
                else None
            )
            if reconciliation_action in {"applied", "keep_workspace"}:
                resume_message["runtime_reconciliation_action"] = cast(
                    str,
                    reconciliation_action,
                )
        pending_calls = _tool_calls(cast(RuntimeLifecycle, lifecycle))
        if waiting_status == "waiting_user" and pending_calls:
            lifecycle["resumed_waiting_request"] = waiting_request
            deferred = lifecycle.get("deferred_resume_messages", [])
            if not isinstance(deferred, list) or any(
                not isinstance(message, Mapping) for message in deferred
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_deferred_resume_messages",
                    "deferred resume messages must be an array of objects",
                )
            lifecycle["deferred_resume_messages"] = [
                *[dict(message) for message in deferred],
                dict(resume_message),
            ]
            lifecycle["next_route"] = "tool"
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        if waiting_status == "waiting_external" and not pending_calls:
            recovered_poll_call = _async_poll_call_from_resume(resume_value)
            if recovered_poll_call is not None:
                lifecycle["pending_tool_calls"] = [recovered_poll_call]
                lifecycle["next_route"] = "tool"
                return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        if waiting_status in {"waiting_agent", "waiting_external"} and pending_calls:
            if waiting_status == "waiting_external":
                lifecycle["next_route"] = "tool"
                return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
            deferred = lifecycle.get("deferred_resume_messages", [])
            if not isinstance(deferred, list) or any(
                not isinstance(message, Mapping) for message in deferred
            ):
                raise RuntimeNodeTransitionError(
                    "invalid_deferred_resume_messages",
                    "deferred resume messages must be an array of objects",
                )
            lifecycle["deferred_resume_messages"] = [
                *[dict(message) for message in deferred],
                dict(resume_message),
            ]
            lifecycle["next_route"] = "tool"
            return {"lifecycle": cast(RuntimeLifecycle, lifecycle)}
        _schedule_compact(lifecycle)
        return {
            "lifecycle": cast(RuntimeLifecycle, lifecycle),
            "messages": [resume_message],
        }

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
        if node == "compact":
            return await self._compact(state, context)
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
                    "terminal node requires a terminal lifecycle",
                )
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
    "NoopRuntimeRunCompactor",
    "RunCompactResult",
    "RuntimeCancelSource",
    "RuntimeFinalizer",
    "RuntimeInvocationCancelled",
    "RuntimeModelStepService",
    "RuntimeNodeTransitionError",
    "RuntimeRunCompactor",
    "RuntimeToolStepService",
    "RuntimeVerifier",
    "ToolStepResult",
    "VerificationResult",
]
