"""Deterministic Runtime node transitions around injected model and tool services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from typing import Literal, Protocol, cast
import uuid

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
from app.services.llm.finish import FINISH_PROTOCOL_REMINDER


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

    def __init__(self, signal: "CancelSignal") -> None:
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
    waiting_request: JsonObject | None = None
    finish_content: str | None = None
    finish_mention_participant_ids: tuple[str, ...] = ()
    finish_delivery_intent: JsonObject | None = None
    repair_instruction: str | None = None
    repair_code: str | None = None
    error: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ToolStepResult:
    """One sequential, receipt-backed tool batch outcome."""

    messages: tuple[JsonObject, ...] = ()
    waiting_request: JsonObject | None = None
    pending_tool_calls: tuple[JsonObject, ...] = ()
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


def _resume_message_content(resume_value: Mapping[str, JsonValue]) -> str:
    resume_type = resume_value.get("resume_type")
    payload = resume_value.get("payload")
    if resume_type == "user_input" and isinstance(payload, Mapping):
        content = payload.get("content")
        if isinstance(content, str):
            return content
    return json.dumps(
        resume_value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    )


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
        result = await self._run_compactor.compact_if_needed(
            state,
            context,
        )
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
                if repairs.get(repair_code, 0) >= 1:
                    violation_code = (
                        "finish_protocol_violation"
                        if repair_code == "missing_finish"
                        else "model_tool_protocol_violation"
                    )
                    lifecycle.update(
                        {
                            "status": "failed",
                            "next_route": "terminal",
                            "reason": violation_code,
                            "pending_tool_calls": [],
                            "error": _error(
                                violation_code,
                                (
                                    f"The model repeated the {repair_code!r} protocol "
                                    "error after one bounded repair. Native tool "
                                    "calling is not working for this Run."
                                ),
                            ),
                        }
                    )
                else:
                    repairs[repair_code] = repairs.get(repair_code, 0) + 1
                    new_messages.append(
                        {
                            "id": _runtime_message_id(
                                context,
                                f"model-step:{step_count}:repair",
                            ),
                            "role": "user",
                            "content": (
                                result.repair_instruction
                                or FINISH_PROTOCOL_REMINDER
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
        pending_calls = (*result.pending_tool_calls, *tail_calls)
        lifecycle = dict(state["lifecycle"])
        lifecycle.update(
            {
                "pending_tool_calls": [dict(call) for call in pending_calls],
            }
        )
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
                    "waiting_request": None,
                    "error": None,
                }
            )
            if pending_calls:
                lifecycle["next_route"] = "tool"
            else:
                _schedule_compact(lifecycle)
        update: RuntimeStateUpdate = {
            "lifecycle": cast(RuntimeLifecycle, lifecycle),
        }
        output_messages = [
            _message_for_channel(dict(message)) for message in result.messages
        ]
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
            lifecycle.pop("finish_delivery_intent", None)
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
        pending_calls = _tool_calls(cast(RuntimeLifecycle, lifecycle))
        if waiting_status == "waiting_user" and pending_calls:
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
    "RuntimeModelStepService",
    "RuntimeRunCompactor",
    "RuntimeInvocationCancelled",
    "RuntimeNodeTransitionError",
    "RuntimeToolStepService",
    "RuntimeVerifier",
    "ToolStepResult",
    "VerificationResult",
]
