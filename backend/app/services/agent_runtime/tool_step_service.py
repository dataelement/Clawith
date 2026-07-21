"""Receipt-backed sequential tool execution for durable Runtime nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Protocol, cast
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.models.agent import Agent
from app.models.agent_run_event import AgentRunEvent
from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime.a2a_runtime import (
    RuntimeA2AService,
    a2a_waiting_request,
)
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.group_runtime_tools import (
    GROUP_READ_TOOL_NAMES,
    GROUP_TOOL_NAMES,
    GROUP_WORKSPACE_MUTATION_TOOL_NAMES,
    GROUP_WRITE_TOOL_NAMES,
    GroupRuntimeToolError,
    GroupRuntimeToolService,
    GroupWorkspaceReconciliationPending,
    with_group_runtime_tools,
)
from app.services.agent_runtime.node_executor import (
    RuntimeCancelSource,
    ToolStepResult,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RuntimeContext,
    RuntimeGraphState,
    runtime_messages_as_json,
)
from app.services.agent_runtime.tool_execution import (
    RetryableToolNodeError,
    SAFE_READ_MAX_ATTEMPTS,
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionReconciliationPending,
    ToolExecutionReservation,
    execution_outcome,
    mark_expired_safe_read_result_unavailable,
    mark_tool_execution_async_pending,
    mark_tool_execution_failed,
    mark_tool_execution_retry_pending,
    mark_tool_execution_succeeded,
    mark_tool_execution_unknown,
    normalize_tool_outcome,
    reserve_tool_execution,
    sanitize_tool_arguments,
    settle_async_operation_executions,
    takeover_tool_execution_for_reconciliation,
)
from app.services.agent_runtime.tool_result_store import (
    ToolResultReconciler,
    ToolResultStore,
)
from app.services.agent_tools import (
    agentbay_run_scope_id,
    execute_builtin_tool_outcome,
    get_runtime_agent_tools_for_llm,
)
from app.services.builtin_tool_definitions import (
    builtin_cross_space_action,
    builtin_policy,
    builtin_sensitive_paths,
)
_CONTROL_TOOL_NAMES = frozenset({"finish", "wait"})
_HEARTBEAT_PRIVATE_PLAZA_TOOLS = frozenset(
    {"plaza_get_new_posts", "plaza_create_post", "plaza_add_comment"}
)
_HEARTBEAT_PLAZA_LIMITS = {
    "plaza_create_post": 1,
    "plaza_add_comment": 2,
}


async def _insert_runtime_activity(
    db,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    key: str,
    summary: str,
    payload: dict,
) -> None:
    """Commit one idempotent observation beside the durable Tool Ledger fact."""
    await db.execute(
        insert(AgentRunEvent)
        .values(
            id=uuid.uuid5(run_id, f"runtime-activity:{key}"),
            tenant_id=tenant_id,
            run_id=run_id,
            agent_id=None,
            event_type="status_changed",
            summary=summary,
            payload=payload,
            artifact_refs=[],
            idempotency_key=key,
            source_checkpoint_id=None,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
    )


class ToolExecutor(Protocol):
    async def __call__(
        self,
        tool_name: str,
        arguments: dict,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: str = "",
        on_output: object | None = None,
    ) -> ToolExecutionOutcome | str: ...


ToolProvider = Callable[[uuid.UUID], Awaitable[list[dict]]]


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    side_effect_classification: str
    retry_policy: str


def _policy(tool_name: str) -> ToolPolicy:
    if tool_name in GROUP_READ_TOOL_NAMES:
        return ToolPolicy("read", "safe")
    if tool_name in GROUP_WRITE_TOOL_NAMES:
        return ToolPolicy("write", "conditional")
    policy = builtin_policy(tool_name)
    return ToolPolicy(policy["effect"], policy["retry_policy"])


def _tool_name(tool: Mapping[str, object]) -> str | None:
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return None
    name = function.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _allowed_tool_names(tools: Sequence[Mapping[str, object]]) -> frozenset[str]:
    return frozenset(name for name in (_tool_name(tool) for tool in tools) if name)


def _call_fields(call: JsonObject) -> tuple[str, str, dict]:
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ToolExecutionError(
            "invalid_tool_call",
            "Runtime tool call requires a non-empty ID",
        )
    if not isinstance(function, Mapping):
        raise ToolExecutionError(
            "invalid_tool_call",
            "Runtime tool call requires a function object",
        )
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ToolExecutionError(
            "invalid_tool_call",
            "Runtime tool call requires a function name",
        )
    raw_arguments = function.get("arguments", "{}")
    try:
        arguments = (
            json.loads(raw_arguments)
            if isinstance(raw_arguments, str)
            else dict(raw_arguments)
            if isinstance(raw_arguments, Mapping)
            else None
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ToolExecutionError(
            "invalid_tool_call",
            "Runtime tool arguments must be one JSON object",
        ) from exc
    if not isinstance(arguments, dict):
        raise ToolExecutionError(
            "invalid_tool_call",
            "Runtime tool arguments must be one JSON object",
        )
    return call_id.strip(), name.strip(), arguments


def _assistant_message_id(
    state: RuntimeGraphState,
    calls: Sequence[JsonObject],
) -> str:
    ordered_call_ids = [cast(str, call.get("id")) for call in calls if isinstance(call.get("id"), str)]
    call_ids = set(ordered_call_ids)
    if len(ordered_call_ids) != len(calls) or len(call_ids) != len(calls):
        raise ToolExecutionError(
            "invalid_tool_call",
            "pending tool calls require unique non-empty IDs",
        )
    matches = []
    for message in reversed(runtime_messages_as_json(state)):
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        message_call_ids = {
            cast(str, raw.get("id")) for raw in raw_calls if isinstance(raw, Mapping) and isinstance(raw.get("id"), str)
        }
        if call_ids.issubset(message_call_ids):
            matches.append(message)
            break
    if not matches:
        raise ToolExecutionError(
            "tool_exchange_missing_assistant",
            "pending tool calls have no matching assistant message",
        )
    message_id = matches[0].get("id")
    if not isinstance(message_id, str) or not message_id:
        raise ToolExecutionError(
            "tool_exchange_missing_assistant",
            "tool proposal assistant message has no stable ID",
        )
    return message_id


def _result_message_id(run_id: uuid.UUID, call_id: str) -> str:
    return str(uuid.uuid5(run_id, f"tool-result:{call_id}"))


def _tool_execution_lease_owner(command_id: str, call_id: str) -> str:
    """Give every executor/recovery invocation a distinct durable fence token."""
    invocation_id = str(uuid.uuid4())
    prefix = f"runtime:{command_id}:{call_id}"
    return f"{prefix[: 127 - len(invocation_id)]}:{invocation_id}"


def _result_message(
    *,
    run_id: uuid.UUID,
    call_id: str,
    tool_name: str,
    outcome: ToolExecutionOutcome,
) -> JsonObject:
    content = outcome.result_summary or (
        "Tool completed without inline output."
        if outcome.status == "succeeded"
        else "Tool operation is still pending."
        if outcome.status == "pending"
        else "Tool execution failed without a reusable result."
    )
    message: JsonObject = {
        "id": _result_message_id(run_id, call_id),
        "role": "tool",
        "tool_call_id": call_id,
        "name": tool_name,
        "content": content,
        "execution_status": outcome.status,
        "result_ref": outcome.result_ref,
    }
    if outcome.error_code is not None:
        message["error_code"] = outcome.error_code
    if outcome.retryable:
        message["retryable"] = True
    if outcome.artifact_refs:
        message["artifact_refs"] = list(outcome.artifact_refs)
    if outcome.evidence_refs:
        message["evidence_refs"] = list(outcome.evidence_refs)
    return message


def _waiting_request(
    *,
    run_id: uuid.UUID,
    call_id: str,
    requires_confirmation: bool,
    error_code: str | None,
) -> JsonObject:
    return {
        "waiting_type": "user" if requires_confirmation else "external",
        "correlation_id": str(uuid.uuid5(run_id, f"tool-reconcile:{call_id}")),
        "reason": error_code or "tool_reconciliation_required",
        "tool_call_id": call_id,
    }


def _async_poll_schedule_metadata(
    *,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    metadata: Mapping[str, object],
    clock: Callable[[], datetime] | None = None,
) -> dict:
    operation = metadata.get("async_operation")
    if not isinstance(operation, Mapping):
        raise ToolExecutionError(
            "invalid_async_tool_outcome",
            "pending async outcome requires poll instructions",
        )
    poll = operation.get("poll")
    if not isinstance(poll, Mapping):
        raise ToolExecutionError(
            "invalid_async_tool_outcome",
            "pending async outcome requires poll instructions",
        )
    tool_name = poll.get("tool")
    arguments = poll.get("arguments")
    interval_ms = poll.get("interval_ms")
    if (
        not isinstance(tool_name, str)
        or not tool_name.strip()
        or not isinstance(arguments, Mapping)
        or isinstance(interval_ms, bool)
        or not isinstance(interval_ms, int)
        or interval_ms < 0
        or interval_ms > 600_000
    ):
        raise ToolExecutionError(
            "invalid_async_tool_outcome",
            "pending async poll instructions are invalid",
        )
    due_at = (clock or (lambda: datetime.now(UTC)))() + timedelta(
        milliseconds=interval_ms
    )
    return {
        **metadata,
        "async_poll_due_at": due_at.isoformat(),
        "async_poll_correlation_id": str(
            uuid.uuid5(run_id, f"async-poll:{execution_id}")
        ),
        "async_poll_call_id": f"async-poll:{execution_id}",
        "async_poll_scheduled": False,
    }


def _async_pending_step_result(
    *,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    call_id: str,
    tool_name: str,
    outcome: ToolExecutionOutcome,
    prior_messages: Sequence[JsonObject],
    tail_calls: Sequence[JsonObject],
) -> ToolStepResult:
    # Settlement can happen in a separate DB session, so the reservation's ORM
    # instance may not contain the just-persisted poll schedule. The settled
    # outcome is the canonical in-process copy of that same durable metadata.
    metadata = outcome.metadata
    operation = metadata.get("async_operation") if isinstance(metadata, dict) else None
    poll = operation.get("poll") if isinstance(operation, Mapping) else None
    operation_key = (
        operation.get("operation_key") if isinstance(operation, Mapping) else None
    )
    correlation_id = (
        metadata.get("async_poll_correlation_id")
        if isinstance(metadata, dict)
        else None
    )
    poll_call_id = (
        metadata.get("async_poll_call_id") if isinstance(metadata, dict) else None
    )
    if (
        not isinstance(poll, Mapping)
        or not isinstance(operation_key, str)
        or not operation_key
        or not isinstance(correlation_id, str)
        or not correlation_id
        or not isinstance(poll_call_id, str)
        or not poll_call_id
        or not isinstance(poll.get("tool"), str)
        or not isinstance(poll.get("arguments"), Mapping)
    ):
        raise ToolExecutionError(
            "invalid_async_poll_schedule",
            "pending async receipt has no durable poll schedule",
        )
    poll_call: JsonObject = {
        "id": poll_call_id,
        "type": "function",
        "function": {
            "name": cast(str, poll["tool"]),
            "arguments": json.dumps(
                dict(cast(Mapping[str, object], poll["arguments"])),
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    }
    proposal: JsonObject = {
        "id": str(uuid.uuid5(run_id, f"async-poll-proposal:{execution_id}")),
        "role": "assistant",
        "content": "",
        "tool_calls": [poll_call],
        "runtime_intent": "async_poll",
        "runtime_run_id": str(run_id),
    }
    return ToolStepResult(
        messages=(
            *prior_messages,
            _result_message(
                run_id=run_id,
                call_id=call_id,
                tool_name=tool_name,
                outcome=outcome,
            ),
            proposal,
        ),
        waiting_request={
            "waiting_type": "external",
            "correlation_id": correlation_id,
            "reason": "async_tool_poll_pending",
            "tool_call_id": call_id,
            "operation_key": operation_key,
        },
        pending_tool_calls=(poll_call, *tail_calls),
    )


def _heartbeat_tool_limit(
    context: RuntimeContext,
    agent: Agent,
    tool_name: str,
) -> int | None:
    if context.source_type != "heartbeat":
        return None
    is_private = (getattr(agent, "access_mode", None) or "company") != "company"
    if is_private and tool_name in _HEARTBEAT_PRIVATE_PLAZA_TOOLS:
        return 0
    return _HEARTBEAT_PLAZA_LIMITS.get(tool_name)


def _is_group_agent_run(state: RuntimeGraphState) -> bool:
    """Recognize the Group scope already validated into the input snapshot."""
    return isinstance(
        state["snapshots"].initial_input.get("group_context"),
        Mapping,
    )


def _heartbeat_blocked_summary(
    agent: Agent,
    tool_name: str,
    limit: int,
) -> str:
    is_private = (getattr(agent, "access_mode", None) or "company") != "company"
    if is_private and tool_name in _HEARTBEAT_PRIVATE_PLAZA_TOOLS:
        return "[BLOCKED] Private heartbeat Agents cannot use Agent Plaza."
    return (
        f"[BLOCKED] Heartbeat limit reached for {tool_name} "
        f"(maximum {limit})."
    )


class RuntimeToolStepService:
    """Reserve, execute, and settle one model-proposed tool batch in order."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        cancel_source: RuntimeCancelSource,
        tool_provider: ToolProvider = get_runtime_agent_tools_for_llm,
        tool_executor: ToolExecutor = execute_builtin_tool_outcome,
        group_tool_service: GroupRuntimeToolService | None = None,
        a2a_service: RuntimeA2AService | None = None,
        tool_result_store: ToolResultStore | None = None,
        tool_result_reconciler: ToolResultReconciler | None = None,
        lease_ttl_seconds: int = 300,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._session_factory = session_factory
        self._cancel_source = cancel_source
        self._tool_provider = tool_provider
        self._tool_executor = tool_executor
        self._group_tool_service = group_tool_service or GroupRuntimeToolService(
            session_factory=session_factory
        )
        self._a2a_service = a2a_service
        self._tool_result_store = tool_result_store or ToolResultStore(
            session_factory=session_factory
        )
        self._tool_result_reconciler = tool_result_reconciler or ToolResultReconciler(
            session_factory=session_factory,
            result_store=self._tool_result_store,
        )
        self._lease_ttl_seconds = lease_ttl_seconds
        self._inline_result_max_bytes = (
            get_settings().AGENT_RUNTIME_TOOL_RESULT_INLINE_MAX_BYTES
        )

    async def _agent(
        self,
        context: RuntimeContext,
    ) -> Agent:
        try:
            tenant_id = uuid.UUID(context.tenant_id)
            agent_id = uuid.UUID(context.agent_id or "")
        except ValueError as exc:
            raise ToolExecutionError(
                "invalid_runtime_identity",
                "Runtime Context contains an invalid UUID",
            ) from exc
        async with self._session_factory() as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                )
            )
            agent = result.scalar_one_or_none()
        if agent is None or agent.is_expired:
            raise ToolExecutionError(
                "agent_unavailable",
                "Runtime tool Agent is unavailable in this tenant",
            )
        return agent

    async def _reserve(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        call_id: str,
        tool_name: str,
        assistant_message_id: str,
        arguments: dict,
        policy: ToolPolicy,
        lease_owner: str,
        reasoning_content: str = "",
        assistant_content: str = "",
    ) -> ToolExecutionReservation:
        async with self._session_factory() as db:
            async with db.begin():
                reservation = await reserve_tool_execution(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    assistant_message_id=assistant_message_id,
                    arguments=arguments,
                    sanitized_arguments=sanitize_tool_arguments(
                        arguments,
                        sensitive_paths=builtin_sensitive_paths(tool_name),
                    ),
                    request_ref=None,
                    side_effect_classification=cast(str, policy.side_effect_classification),  # type: ignore[arg-type]
                    retry_policy=cast(str, policy.retry_policy),  # type: ignore[arg-type]
                    lease_owner=lease_owner,
                    lease_ttl_seconds=self._lease_ttl_seconds,
                    resume_safe_read=(
                        policy.side_effect_classification == "read"
                        and policy.retry_policy == "safe"
                    ),
                )
                if reasoning_content.strip():
                    await _insert_runtime_activity(
                        db,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        key=f"activity:thinking:{assistant_message_id}",
                        summary="Runtime model reasoning available",
                        payload={
                            "status": "running",
                            "activity_type": "thinking",
                            "content": reasoning_content.strip(),
                            "message_id": assistant_message_id,
                        },
                    )
                if assistant_content.strip():
                    await _insert_runtime_activity(
                        db,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        key=f"activity:progress:{assistant_message_id}",
                        summary="Runtime model progress available",
                        payload={
                            "status": "running",
                            "activity_type": "assistant_progress",
                            "content": assistant_content.strip(),
                            "message_id": assistant_message_id,
                        },
                    )
                await _insert_runtime_activity(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    key=f"activity:tool:{call_id}:running",
                    summary=f"Runtime tool {tool_name} started",
                    payload={
                        "status": "running",
                        "activity_type": "tool_call",
                        "call_id": call_id,
                        "name": tool_name,
                        "args": dict(reservation.execution.sanitized_arguments or {}),
                        "reasoning_content": reasoning_content.strip(),
                        "assistant_message_id": assistant_message_id,
                    },
                )
                return reservation

    async def _settle_outcome(
        self,
        *,
        tenant_id: uuid.UUID,
        reservation: ToolExecutionReservation,
        lease_owner: str,
        policy: ToolPolicy,
        outcome: ToolExecutionOutcome,
    ) -> ToolExecutionOutcome:
        normalized, archive_body = normalize_tool_outcome(
            outcome,
            effect=cast(str, policy.side_effect_classification),  # type: ignore[arg-type]
            retry_policy=cast(str, policy.retry_policy),  # type: ignore[arg-type]
            inline_max_bytes=self._inline_result_max_bytes,
        )
        if normalized.private_binary is not None:
            try:
                receipt = await self._tool_result_store.write_binary(
                    reservation.execution,
                    normalized.private_binary,
                    mime_type=str(
                        normalized.metadata.get("mime_type") or "image/png"
                    ),
                )
                receipt_ref = getattr(receipt, "ref", None)
                if not isinstance(receipt_ref, str) or not receipt_ref:
                    receipt_ref = str(receipt)
                content_hash = getattr(receipt, "content_hash", None)
                mime_type = getattr(receipt, "mime_type", None)
                size = getattr(receipt, "size", None)
                if not isinstance(content_hash, str):
                    content_hash = hashlib.sha256(
                        normalized.private_binary
                    ).hexdigest()
                if not isinstance(mime_type, str):
                    mime_type = str(
                        normalized.metadata.get("mime_type") or "image/png"
                    )
                if not isinstance(size, int):
                    size = len(normalized.private_binary)
            except Exception as exc:
                normalized = ToolExecutionOutcome(
                    status="failed",
                    result_summary=(
                        "Tool screenshot could not be archived privately; "
                        "the provider call will not be repeated."
                    ),
                    result_ref=None,
                    error_code="tool_binary_archive_failed",
                    retryable=False,
                    metadata={
                        **normalized.metadata,
                        "archive_status": "failed",
                        "archive_error_code": type(exc).__name__,
                    },
                )
                archive_body = None
            else:
                normalized = replace(
                    normalized,
                    evidence_refs=tuple(
                        dict.fromkeys(
                            (*normalized.evidence_refs, receipt_ref)
                        )
                    ),
                    metadata={
                        **normalized.metadata,
                        "content_hash": content_hash,
                        "mime_type": mime_type,
                        "size": size,
                        "archive_status": "stored",
                    },
                    private_binary=None,
                )
        if archive_body is not None and normalized.status == "succeeded":
            try:
                result_ref = await self._tool_result_store.write(
                    reservation.execution,
                    normalized,
                    archive_body,
                )
            except Exception as exc:
                archive_metadata = {
                    **normalized.metadata,
                    "archive_status": "failed",
                    "archive_error_code": type(exc).__name__,
                }
                if policy.side_effect_classification == "read":
                    normalized = ToolExecutionOutcome(
                        status="failed",
                        result_summary=(
                            "Tool result could not be archived; the provider "
                            "call will not be repeated."
                        ),
                        result_ref=None,
                        error_code="tool_result_archive_failed",
                        retryable=False,
                        metadata=archive_metadata,
                    )
                else:
                    normalized = replace(
                        normalized,
                        result_ref=None,
                        metadata=archive_metadata,
                    )
            else:
                normalized = replace(
                    normalized,
                    result_ref=result_ref,
                    metadata={
                        **normalized.metadata,
                        "archive_status": "stored",
                    },
                )
        elif archive_body is not None:
            normalized = replace(
                normalized,
                metadata={
                    **normalized.metadata,
                    "archive_status": "not_stored_for_non_success",
                },
            )

        raw_attempt_count = getattr(reservation.execution, "attempt_count", 1)
        attempt_count = (
            raw_attempt_count
            if isinstance(raw_attempt_count, int)
            and not isinstance(raw_attempt_count, bool)
            and raw_attempt_count >= 1
            else 1
        )
        normalized = replace(
            normalized,
            metadata={
                **normalized.metadata,
                "runtime_attempt_count": attempt_count,
            },
        )
        if normalized.status == "pending":
            normalized = replace(
                normalized,
                metadata=_async_poll_schedule_metadata(
                    run_id=reservation.execution.run_id,
                    execution_id=reservation.execution.id,
                    metadata=normalized.metadata,
                ),
            )
            async with self._session_factory() as db:
                async with db.begin():
                    execution = await mark_tool_execution_async_pending(
                        db,
                        tenant_id=tenant_id,
                        execution_id=reservation.execution.id,
                        lease_owner=lease_owner,
                        result_summary=normalized.result_summary,
                        metadata=normalized.metadata,
                    )
                    await _insert_runtime_activity(
                        db,
                        tenant_id=tenant_id,
                        run_id=reservation.execution.run_id,
                        key=f"activity:tool:{reservation.execution.tool_call_id}:pending",
                        summary=f"Runtime tool {reservation.execution.tool_name} pending",
                        payload={
                            "status": "running",
                            "activity_type": "tool_call",
                            "call_id": reservation.execution.tool_call_id,
                            "name": reservation.execution.tool_name,
                            "args": dict(reservation.execution.sanitized_arguments or {}),
                            "result": execution.result_summary or "",
                            "execution_status": "pending",
                        },
                    )
            return replace(
                normalized,
                result_summary=execution.result_summary,
                result_ref=execution.result_ref,
                metadata=(
                    dict(execution.result_metadata)
                    if isinstance(execution.result_metadata, dict)
                    else normalized.metadata
                ),
            )
        if normalized.retryable and attempt_count < SAFE_READ_MAX_ATTEMPTS:
            async with self._session_factory() as db:
                async with db.begin():
                    await mark_tool_execution_retry_pending(
                        db,
                        tenant_id=tenant_id,
                        execution_id=reservation.execution.id,
                        lease_owner=lease_owner,
                        result_summary=normalized.result_summary,
                        error_code=normalized.error_code,
                        metadata=normalized.metadata,
                    )
            raise RetryableToolNodeError(
                tool_call_id=reservation.execution.tool_call_id,
                error_code=normalized.error_code,
            )
        if normalized.retryable:
            last_error_code = normalized.error_code
            prior_summary = normalized.result_summary or (
                "The safe read tool failed without a reusable result."
            )
            normalized = replace(
                normalized,
                result_summary=(
                    f"{prior_summary}\n\n"
                    f"Runtime automatic retries were exhausted after "
                    f"{attempt_count} attempts. Do not repeat the identical "
                    "tool call unchanged."
                ),
                error_code="tool_retry_exhausted",
                retryable=False,
                metadata={
                    **normalized.metadata,
                    "last_error_code": last_error_code,
                    "runtime_retry_exhausted": True,
                    "runtime_retry_pending": False,
                },
            )

        async with self._session_factory() as db:
            async with db.begin():
                operation = normalized.metadata.get("async_operation")
                terminal_async = (
                    normalized.status in {"succeeded", "failed", "unknown"}
                    and normalized.metadata.get("runtime_async_pending") is False
                    and isinstance(operation, Mapping)
                    and isinstance(operation.get("operation_key"), str)
                    and bool(operation.get("operation_key"))
                )
                if terminal_async:
                    execution = await settle_async_operation_executions(
                        db,
                        tenant_id=tenant_id,
                        run_id=reservation.execution.run_id,
                        execution_id=reservation.execution.id,
                        lease_owner=lease_owner,
                        status=normalized.status,
                        result_summary=normalized.result_summary,
                        result_ref=normalized.result_ref,
                        error_code=normalized.error_code,
                        retryable=normalized.retryable,
                        artifact_refs=normalized.artifact_refs,
                        evidence_refs=normalized.evidence_refs,
                        metadata=normalized.metadata,
                    )
                else:
                    settle = {
                        "succeeded": mark_tool_execution_succeeded,
                        "failed": mark_tool_execution_failed,
                        "unknown": mark_tool_execution_unknown,
                    }[normalized.status]
                    execution = await settle(
                        db,
                        tenant_id=tenant_id,
                        execution_id=reservation.execution.id,
                        lease_owner=lease_owner,
                        result_summary=normalized.result_summary,
                        result_ref=normalized.result_ref,
                        error_code=normalized.error_code,
                        retryable=normalized.retryable,
                        artifact_refs=normalized.artifact_refs,
                        evidence_refs=normalized.evidence_refs,
                        metadata=normalized.metadata,
                    )
                await _insert_runtime_activity(
                    db,
                    tenant_id=tenant_id,
                    run_id=reservation.execution.run_id,
                    key=(
                        f"activity:tool:{reservation.execution.tool_call_id}:"
                        f"{normalized.status}"
                    ),
                    summary=(
                        f"Runtime tool {reservation.execution.tool_name} "
                        f"{normalized.status}"
                    ),
                    payload={
                        "status": "done",
                        "activity_type": "tool_call",
                        "call_id": reservation.execution.tool_call_id,
                        "name": reservation.execution.tool_name,
                        "args": dict(reservation.execution.sanitized_arguments or {}),
                        "result": execution.result_summary or "",
                        "execution_status": normalized.status,
                        "error_code": normalized.error_code,
                    },
                )
        return replace(
            normalized,
            result_summary=execution.result_summary,
            result_ref=execution.result_ref,
        )

    async def _takeover_for_reconciliation(
        self,
        *,
        tenant_id: uuid.UUID,
        reservation: ToolExecutionReservation,
        lease_owner: str,
    ):
        async with self._session_factory() as db:
            async with db.begin():
                return await takeover_tool_execution_for_reconciliation(
                    db,
                    tenant_id=tenant_id,
                    execution_id=reservation.execution.id,
                    lease_owner=lease_owner,
                    lease_ttl_seconds=self._lease_ttl_seconds,
                )

    async def _mark_exception(
        self,
        *,
        tenant_id: uuid.UUID,
        reservation: ToolExecutionReservation,
        lease_owner: str,
        policy: ToolPolicy,
        exc: Exception,
    ) -> ToolExecutionOutcome:
        known_failure = (
            policy.side_effect_classification == "read"
            or isinstance(exc, (GroupRuntimeToolError, ToolExecutionError))
        )
        return await self._settle_outcome(
            tenant_id=tenant_id,
            reservation=reservation,
            lease_owner=lease_owner,
            policy=policy,
            outcome=ToolExecutionOutcome(
                status="failed" if known_failure else "unknown",
                result_summary=f"{type(exc).__name__}: tool execution failed",
                result_ref=None,
                error_code=(
                    exc.code
                    if isinstance(exc, (GroupRuntimeToolError, ToolExecutionError))
                    else "tool_execution_exception"
                ),
                # Automatic Runtime retry requires a typed provider outcome
                # with retryable=true. An unclassified Python exception may be
                # a bad argument, missing file, permission error, or code bug.
                retryable=False,
                metadata={"error_class": type(exc).__name__},
            ),
        )

    def _group_unknown_failure(
        self,
        *,
        run_id: uuid.UUID,
        call_id: str,
        tool_name: str,
        policy: ToolPolicy,
        outcome: ToolExecutionOutcome,
        messages: Sequence[JsonObject],
        pending_tool_calls: Sequence[JsonObject],
    ) -> ToolStepResult:
        """End an unresumable Group Run without creating a user interrupt."""
        normalized, _ = normalize_tool_outcome(
            outcome,
            effect=cast(str, policy.side_effect_classification),  # type: ignore[arg-type]
            retry_policy=cast(str, policy.retry_policy),  # type: ignore[arg-type]
            inline_max_bytes=self._inline_result_max_bytes,
        )
        if normalized.status != "unknown":
            raise ToolExecutionError(
                "invalid_group_tool_outcome",
                "Group unknown-outcome handling requires an unknown ledger fact",
            )
        error_code = normalized.error_code or "tool_outcome_unknown"
        error_message = normalized.result_summary or (
            "Tool outcome is unknown; confirm the external result before starting "
            "a new Group Run."
        )
        return ToolStepResult(
            messages=(
                *messages,
                _result_message(
                    run_id=run_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    outcome=normalized,
                ),
            ),
            pending_tool_calls=tuple(pending_tool_calls),
            error={"code": error_code, "message": error_message},
        )

    async def _successful_tool_count(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        tool_name: str,
    ) -> int:
        async with self._session_factory() as db:
            result = await db.execute(
                select(func.count(AgentToolExecution.id)).where(
                    AgentToolExecution.tenant_id == tenant_id,
                    AgentToolExecution.run_id == run_id,
                    AgentToolExecution.tool_name == tool_name,
                    AgentToolExecution.status == "succeeded",
                )
            )
            return int(result.scalar_one())

    async def _mark_policy_blocked(
        self,
        *,
        tenant_id: uuid.UUID,
        reservation: ToolExecutionReservation,
        lease_owner: str,
        policy: ToolPolicy,
        result_summary: str,
    ) -> ToolExecutionOutcome:
        return await self._settle_outcome(
            tenant_id=tenant_id,
            reservation=reservation,
            lease_owner=lease_owner,
            policy=policy,
            outcome=ToolExecutionOutcome(
                status="failed",
                result_summary=result_summary,
                result_ref=None,
                error_code="tool_policy_blocked",
            ),
        )

    async def execute_pending(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        tool_calls: tuple[JsonObject, ...],
    ) -> ToolStepResult:
        try:
            tenant_id = uuid.UUID(context.tenant_id)
            run_id = uuid.UUID(context.run_id)
            agent = await self._agent(context)
            assistant_message_id = _assistant_message_id(state, tool_calls)
            assistant_message = next(
                (
                    message
                    for message in runtime_messages_as_json(state)
                    if message.get("id") == assistant_message_id
                ),
                {},
            )
            reasoning_content = (
                str(assistant_message.get("reasoning_content") or "")
                if isinstance(assistant_message, Mapping)
                else ""
            )
            assistant_content = (
                str(assistant_message.get("content") or "")
                if isinstance(assistant_message, Mapping)
                else ""
            )
            allowed_names = _allowed_tool_names(
                with_group_runtime_tools(
                    await self._tool_provider(agent.id),
                    state,
                )
            )
            messages: list[JsonObject] = []
            for index, call in enumerate(tool_calls):
                cancel = await self._cancel_source.get_cancel(state, context)
                if cancel is not None:
                    return ToolStepResult(
                        messages=tuple(messages),
                        cancel_signal=cancel,
                    )
                call_id, tool_name, arguments = _call_fields(call)
                if tool_name in _CONTROL_TOOL_NAMES or tool_name not in allowed_names:
                    raise ToolExecutionError(
                        "tool_not_enabled",
                        f"tool {tool_name!r} is not enabled for this Agent",
                    )
                policy = _policy(tool_name)
                lease_owner = _tool_execution_lease_owner(
                    context.command_id,
                    call_id,
                )
                reservation = await self._reserve(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    assistant_message_id=assistant_message_id,
                    arguments=arguments,
                    policy=policy,
                    lease_owner=lease_owner,
                    reasoning_content=reasoning_content,
                    assistant_content=assistant_content,
                )
                if reservation.reusable_result is not None:
                    if reservation.reusable_result.status == "pending":
                        return _async_pending_step_result(
                            run_id=run_id,
                            execution_id=reservation.execution.id,
                            call_id=call_id,
                            tool_name=tool_name,
                            outcome=reservation.reusable_result,
                            prior_messages=messages,
                            tail_calls=tool_calls[index + 1 :],
                        )
                    messages.append(
                        _result_message(
                            run_id=run_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            outcome=reservation.reusable_result,
                        )
                    )
                    async with self._session_factory() as db:
                        async with db.begin():
                            reused = reservation.reusable_result
                            await _insert_runtime_activity(
                                db,
                                tenant_id=tenant_id,
                                run_id=run_id,
                                key=f"activity:tool:{call_id}:{reused.status}",
                                summary=f"Runtime tool {tool_name} {reused.status}",
                                payload={
                                    "status": "done",
                                    "activity_type": "tool_call",
                                    "call_id": call_id,
                                    "name": tool_name,
                                    "args": dict(reservation.execution.sanitized_arguments or {}),
                                    "result": reused.result_summary or "",
                                    "execution_status": reused.status,
                                    "error_code": reused.error_code,
                                },
                            )
                    if tool_name == "send_message_to_agent" and self._a2a_service:
                        waiting_request = a2a_waiting_request(
                            source_run_id=run_id,
                            tool_call_id=call_id,
                            arguments=arguments,
                            result_ref=reservation.reusable_result.result_ref,
                        )
                        if waiting_request is not None:
                            return ToolStepResult(
                                messages=tuple(messages),
                                waiting_request=waiting_request,
                                pending_tool_calls=tool_calls[index + 1 :],
                            )
                    continue
                if reservation.blocked:
                    if reservation.prior_failure is not None:
                        messages.append(
                            _result_message(
                                run_id=run_id,
                                call_id=call_id,
                                tool_name=tool_name,
                                outcome=reservation.prior_failure,
                            )
                        )
                        continue
                    if (
                        reservation.error_code
                        == "safe_read_result_reconciliation_required"
                    ):
                        reconciliation = (
                            await self._tool_result_reconciler.reconcile_candidate(
                                reservation.execution
                            )
                        )
                        if (
                            reconciliation.status == "reconciled"
                            and reconciliation.outcome is not None
                        ):
                            messages.append(
                                _result_message(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    outcome=reconciliation.outcome,
                                )
                            )
                            continue
                        if reconciliation.status == "unavailable":
                            try:
                                async with self._session_factory() as db:
                                    async with db.begin():
                                        execution = (
                                            await mark_expired_safe_read_result_unavailable(
                                                db,
                                                tenant_id=tenant_id,
                                                execution_id=reservation.execution.id,
                                                probe_error_code=(
                                                    reconciliation.error_code
                                                    or "tool_result_unavailable"
                                                ),
                                            )
                                        )
                            except Exception as exc:
                                raise ToolExecutionReconciliationPending(
                                    (
                                        exc.code
                                        if isinstance(exc, ToolExecutionError)
                                        else "safe_read_result_reconciliation_pending"
                                    ),
                                    str(exc),
                                    defer_without_attempt=True,
                                ) from exc
                            messages.append(
                                _result_message(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    outcome=execution_outcome(execution),
                                )
                            )
                            continue
                        raise ToolExecutionReconciliationPending(
                            "safe_read_result_reconciliation_pending",
                            "Safe read result reconciliation has not settled yet",
                            defer_without_attempt=True,
                        )
                    if (
                        reservation.execution.status == "started"
                        and policy.side_effect_classification == "read"
                        and policy.retry_policy == "safe"
                    ):
                        raise ToolExecutionReconciliationPending(
                            "safe_read_attempt_active",
                            "A safe read attempt still owns the active receipt",
                            defer_without_attempt=True,
                        )
                    if (
                        tool_name in GROUP_WORKSPACE_MUTATION_TOOL_NAMES
                        and reservation.execution.status == "started"
                    ):
                        takeover = await self._takeover_for_reconciliation(
                            tenant_id=tenant_id,
                            reservation=reservation,
                            lease_owner=lease_owner,
                        )
                        if takeover.active:
                            raise GroupWorkspaceReconciliationPending(
                                "Group workspace operation still has an active executor",
                                code="group_workspace_active_lease",
                                defer_without_attempt=True,
                            )
                        if takeover.terminal_outcome is not None:
                            outcome = takeover.terminal_outcome
                            if outcome.status == "unknown":
                                return self._group_unknown_failure(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    policy=policy,
                                    outcome=outcome,
                                    messages=messages,
                                    pending_tool_calls=tool_calls[index + 1 :],
                                )
                            messages.append(
                                _result_message(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    outcome=outcome,
                                )
                            )
                            continue
                        if not takeover.acquired:
                            raise GroupWorkspaceReconciliationPending(
                                "Group workspace operation could not acquire a recovery fence",
                                code="group_workspace_fence_unavailable",
                                defer_without_attempt=True,
                            )
                        outcome = (
                            await self._group_tool_service.reconcile_workspace_operation(
                                state,
                                context,
                                agent,
                                tool_name,
                                arguments,
                                operation_id=reservation.execution.id,
                                lease_owner=lease_owner,
                            )
                        )
                        outcome = await self._settle_outcome(
                            tenant_id=tenant_id,
                            reservation=reservation,
                            lease_owner=lease_owner,
                            policy=policy,
                            outcome=outcome,
                        )
                        if outcome.status == "unknown":
                            return self._group_unknown_failure(
                                run_id=run_id,
                                call_id=call_id,
                                tool_name=tool_name,
                                policy=policy,
                                outcome=outcome,
                                messages=messages,
                                pending_tool_calls=tool_calls[index + 1 :],
                            )
                        messages.append(
                            _result_message(
                                run_id=run_id,
                                call_id=call_id,
                                tool_name=tool_name,
                                outcome=outcome,
                            )
                        )
                        continue
                    if (
                        _is_group_agent_run(state)
                        and reservation.requires_confirmation
                    ):
                        return self._group_unknown_failure(
                            run_id=run_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            policy=policy,
                            outcome=execution_outcome(reservation.execution),
                            messages=messages,
                            pending_tool_calls=tool_calls[index + 1 :],
                        )
                    return ToolStepResult(
                        messages=tuple(messages),
                        waiting_request=_waiting_request(
                            run_id=run_id,
                            call_id=call_id,
                            requires_confirmation=reservation.requires_confirmation,
                            error_code=reservation.error_code,
                        ),
                        pending_tool_calls=tool_calls[index:],
                    )

                canonical_cross_space_action = builtin_cross_space_action(tool_name)
                if (
                    _is_group_agent_run(state)
                    and canonical_cross_space_action is not None
                ):
                    outcome = await self._settle_outcome(
                        tenant_id=tenant_id,
                        reservation=reservation,
                        lease_owner=lease_owner,
                        policy=policy,
                        outcome=ToolExecutionOutcome(
                            status="failed",
                            result_summary=(
                                "Group cross-space actions require an explicit "
                                "human-approved grant; no provider action was executed."
                            ),
                            result_ref=None,
                            error_code=(
                                "group_cross_space_confirmation_required"
                            ),
                            retryable=False,
                            metadata={
                                "canonical_action": canonical_cross_space_action,
                            },
                        ),
                    )
                    messages.append(
                        _result_message(
                            run_id=run_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            outcome=outcome,
                        )
                    )
                    continue

                if tool_name == "send_message_to_agent" and self._a2a_service:
                    try:
                        actor_user_id = (
                            uuid.UUID(context.actor_user_id)
                            if context.actor_user_id
                            else None
                        )
                        a2a_result = await self._a2a_service.execute(
                            tenant_id=tenant_id,
                            source_run_id=run_id,
                            source_agent_id=agent.id,
                            tool_call_id=call_id,
                            arguments=arguments,
                            reservation=reservation,
                            lease_owner=lease_owner,
                            actor_user_id=actor_user_id,
                        )
                    except Exception as exc:
                        outcome = await self._mark_exception(
                            tenant_id=tenant_id,
                            reservation=reservation,
                            lease_owner=lease_owner,
                            policy=policy,
                            exc=exc,
                        )
                        if outcome.status == "unknown":
                            if _is_group_agent_run(state):
                                return self._group_unknown_failure(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    policy=policy,
                                    outcome=outcome,
                                    messages=messages,
                                    pending_tool_calls=tool_calls[index + 1 :],
                                )
                            return ToolStepResult(
                                messages=tuple(messages),
                                waiting_request=_waiting_request(
                                    run_id=run_id,
                                    call_id=call_id,
                                    requires_confirmation=True,
                                    error_code="tool_outcome_unknown",
                                ),
                                pending_tool_calls=tool_calls[index:],
                            )
                    else:
                        if a2a_result is not None:
                            if (
                                _is_group_agent_run(state)
                                and a2a_result.outcome.status == "unknown"
                            ):
                                return self._group_unknown_failure(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    policy=policy,
                                    outcome=a2a_result.outcome,
                                    messages=messages,
                                    pending_tool_calls=tool_calls[index + 1 :],
                                )
                            messages.append(
                                _result_message(
                                    run_id=run_id,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    outcome=a2a_result.outcome,
                                )
                            )
                            if a2a_result.waiting_request is not None:
                                return ToolStepResult(
                                    messages=tuple(messages),
                                    waiting_request=a2a_result.waiting_request,
                                    pending_tool_calls=tool_calls[index + 1 :],
                                )
                            continue

                heartbeat_limit = _heartbeat_tool_limit(context, agent, tool_name)
                if heartbeat_limit is not None:
                    successful_count = (
                        0
                        if heartbeat_limit == 0
                        else await self._successful_tool_count(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            tool_name=tool_name,
                        )
                    )
                    if successful_count >= heartbeat_limit:
                        outcome = await self._mark_policy_blocked(
                            tenant_id=tenant_id,
                            reservation=reservation,
                            lease_owner=lease_owner,
                            policy=policy,
                            result_summary=_heartbeat_blocked_summary(
                                agent,
                                tool_name,
                                heartbeat_limit,
                            ),
                        )
                        messages.append(
                            _result_message(
                                run_id=run_id,
                                call_id=call_id,
                                tool_name=tool_name,
                                outcome=outcome,
                            )
                        )
                        continue

                try:
                    if tool_name in GROUP_TOOL_NAMES:
                        if tool_name in GROUP_WORKSPACE_MUTATION_TOOL_NAMES:
                            raw_result = await self._group_tool_service.execute(
                                state,
                                context,
                                agent,
                                tool_name,
                                arguments,
                                operation_id=reservation.execution.id,
                                lease_owner=lease_owner,
                            )
                        else:
                            raw_result = await self._group_tool_service.execute(
                                state,
                                context,
                                agent,
                                tool_name,
                                arguments,
                            )
                    else:
                        agentbay_run_token = None
                        if tool_name.startswith("agentbay_"):
                            agentbay_run_token = agentbay_run_scope_id.set(
                                context.run_id
                            )
                        try:
                            raw_result = await self._tool_executor(
                                tool_name,
                                arguments,
                                agent.id,
                                context.actor_user_id and uuid.UUID(context.actor_user_id) or agent.creator_id,
                                context.session_id or "",
                            )
                        finally:
                            if agentbay_run_token is not None:
                                agentbay_run_scope_id.reset(agentbay_run_token)
                except GroupWorkspaceReconciliationPending:
                    raise
                except Exception as exc:
                    outcome = await self._mark_exception(
                        tenant_id=tenant_id,
                        reservation=reservation,
                        lease_owner=lease_owner,
                        policy=policy,
                        exc=exc,
                    )
                    if outcome.status == "unknown":
                        if _is_group_agent_run(state):
                            return self._group_unknown_failure(
                                run_id=run_id,
                                call_id=call_id,
                                tool_name=tool_name,
                                policy=policy,
                                outcome=outcome,
                                messages=messages,
                                pending_tool_calls=tool_calls[index + 1 :],
                            )
                        return ToolStepResult(
                            messages=tuple(messages),
                            waiting_request=_waiting_request(
                                run_id=run_id,
                                call_id=call_id,
                                requires_confirmation=True,
                                error_code="tool_outcome_unknown",
                            ),
                            pending_tool_calls=tool_calls[index:],
                        )
                else:
                    if isinstance(raw_result, ToolExecutionOutcome):
                        proposed_outcome = raw_result
                    else:
                        proposed_outcome = ToolExecutionOutcome(
                            status=(
                                "failed"
                                if policy.side_effect_classification == "read"
                                else "unknown"
                            ),
                            result_summary=(
                                "Tool handler returned an untyped result; its "
                                "business outcome was not accepted."
                            ),
                            result_ref=None,
                            error_code="untyped_tool_outcome",
                            retryable=False,
                            metadata={"error_class": type(raw_result).__name__},
                        )
                    # Settlement stays outside the handler-exception block. If
                    # private archive succeeds and DB settlement fails, the
                    # receipt remains started for reconciliation; it must not
                    # be rewritten as a fresh handler failure.
                    try:
                        outcome = await self._settle_outcome(
                            tenant_id=tenant_id,
                            reservation=reservation,
                            lease_owner=lease_owner,
                            policy=policy,
                            outcome=proposed_outcome,
                        )
                    except Exception as exc:
                        if tool_name in GROUP_WORKSPACE_MUTATION_TOOL_NAMES:
                            raise GroupWorkspaceReconciliationPending(
                                "Group workspace ledger settlement requires reconciliation"
                            ) from exc
                        raise
                if outcome.status == "pending":
                    return _async_pending_step_result(
                        run_id=run_id,
                        execution_id=reservation.execution.id,
                        call_id=call_id,
                        tool_name=tool_name,
                        outcome=outcome,
                        prior_messages=messages,
                        tail_calls=tool_calls[index + 1 :],
                    )
                if outcome.status == "unknown":
                    if _is_group_agent_run(state):
                        return self._group_unknown_failure(
                            run_id=run_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            policy=policy,
                            outcome=outcome,
                            messages=messages,
                            pending_tool_calls=tool_calls[index + 1 :],
                        )
                    return ToolStepResult(
                        messages=tuple(messages),
                        waiting_request=_waiting_request(
                            run_id=run_id,
                            call_id=call_id,
                            requires_confirmation=True,
                            error_code=outcome.error_code or "tool_outcome_unknown",
                        ),
                        pending_tool_calls=tool_calls[index:],
                    )
                messages.append(
                    _result_message(
                        run_id=run_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        outcome=outcome,
                    )
                )
            return ToolStepResult(messages=tuple(messages))
        except (
            GroupWorkspaceReconciliationPending,
            RetryableToolNodeError,
            ToolExecutionReconciliationPending,
        ):
            raise
        except ToolExecutionError as exc:
            return ToolStepResult(
                error={"code": exc.code, "message": str(exc)},
            )
        except Exception as exc:
            return ToolStepResult(
                error={
                    "code": "tool_execution_failed",
                    "message": f"Runtime tool step failed: {type(exc).__name__}",
                }
            )


__all__ = ["RuntimeToolStepService", "ToolPolicy"]
