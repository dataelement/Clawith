"""Durable idempotency decisions for Runtime tool executions.

The ledger is deliberately narrower than a trace system.  It answers one
question before a tool node performs work: may this exact model tool call be
executed, or must the Runtime reuse/reconcile an earlier outcome?
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any, Callable, Literal
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.agent_tool_execution import AgentToolExecution


ToolExecutionStatus = Literal[
    "not_started",
    "started",
    "succeeded",
    "failed",
    "unknown",
]
SideEffectClassification = Literal["read", "write", "external_write"]
RetryPolicy = Literal["safe", "conditional", "never"]

_PERSISTED_STATUSES = frozenset({"started", "succeeded", "failed", "unknown"})
_SIDE_EFFECT_CLASSIFICATIONS = frozenset({"read", "write", "external_write"})
_RETRY_POLICIES = frozenset({"safe", "conditional", "never"})
_METADATA_KEY = "__clawith_tool_execution__"
_METADATA_VERSION = 1


class ToolExecutionError(RuntimeError):
    """A stable tool-ledger contract was rejected without executing the tool."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """The durable, safe-to-reuse portion of a completed tool outcome."""

    status: Literal["succeeded", "failed", "unknown"]
    result_summary: str | None
    result_ref: str | None


@dataclass(frozen=True, slots=True)
class ToolExecutionInspection:
    """Current ledger state; ``not_started`` is represented by no table row."""

    status: ToolExecutionStatus
    execution: AgentToolExecution | None


@dataclass(frozen=True, slots=True)
class ToolExecutionReservation:
    """Deterministic decision returned before a caller executes a tool."""

    execution: AgentToolExecution
    created: bool
    retrying: bool
    reusable_result: ToolExecutionOutcome | None
    prior_failure: ToolExecutionOutcome | None
    blocked: bool
    reconciliation_required: bool
    requires_confirmation: bool
    error_code: str | None

    @property
    def status(self) -> str:
        return self.execution.status

    @property
    def can_execute(self) -> bool:
        """True only for a newly persisted reservation or an explicit safe retry."""
        return not self.blocked and self.reusable_result is None


def _require_text(value: str, *, field: str, max_length: int) -> None:
    if not value or not value.strip():
        raise ToolExecutionError("invalid_tool_execution_input", f"{field} must not be blank")
    if len(value) > max_length:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"{field} exceeds its {max_length}-character storage limit",
        )


def _require_optional_text(value: str | None, *, field: str, max_length: int) -> None:
    if value is not None:
        _require_text(value, field=field, max_length=max_length)


def _json_copy(value: dict[str, Any], *, field: str) -> dict[str, Any]:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"{field} must be a JSON object with finite values",
        ) from exc
    if not isinstance(copied, dict):
        raise ToolExecutionError("invalid_tool_execution_input", f"{field} must be a JSON object")
    return copied


def fingerprint_arguments(arguments: dict[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint without persisting raw arguments."""
    canonical = json.dumps(
        _json_copy(arguments, field="arguments"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _stored_arguments(
    sanitized_arguments: dict[str, Any] | None,
    *,
    side_effect_classification: str,
    retry_policy: str,
) -> dict[str, Any]:
    safe_arguments = (
        _json_copy(sanitized_arguments, field="sanitized_arguments") if sanitized_arguments is not None else None
    )
    return {
        "arguments": safe_arguments,
        _METADATA_KEY: {
            "version": _METADATA_VERSION,
            "side_effect_classification": side_effect_classification,
            "retry_policy": retry_policy,
        },
    }


def _execution_metadata(execution: AgentToolExecution) -> tuple[str, str]:
    stored = execution.sanitized_arguments
    metadata = stored.get(_METADATA_KEY) if isinstance(stored, dict) else None
    if not isinstance(metadata, dict) or metadata.get("version") != _METADATA_VERSION:
        # Old or malformed rows are treated as external writes.  This is the
        # conservative boundary for reconciliation and never enables a retry.
        return "external_write", "never"
    effect = metadata.get("side_effect_classification")
    retry_policy = metadata.get("retry_policy")
    if effect not in _SIDE_EFFECT_CLASSIFICATIONS or retry_policy not in _RETRY_POLICIES:
        return "external_write", "never"
    return str(effect), str(retry_policy)


def _validate_request(
    *,
    tool_call_id: str,
    tool_name: str,
    assistant_message_id: str,
    side_effect_classification: str,
    retry_policy: str,
    request_ref: str | None,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> None:
    _require_text(tool_call_id, field="tool_call_id", max_length=255)
    _require_text(tool_name, field="tool_name", max_length=200)
    _require_text(assistant_message_id, field="assistant_message_id", max_length=255)
    _require_text(lease_owner, field="lease_owner", max_length=128)
    _require_optional_text(request_ref, field="request_ref", max_length=500)
    if side_effect_classification not in _SIDE_EFFECT_CLASSIFICATIONS:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"unsupported side_effect_classification: {side_effect_classification}",
        )
    if retry_policy not in _RETRY_POLICIES:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"unsupported retry_policy: {retry_policy}",
        )
    if lease_ttl_seconds <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "lease_ttl_seconds must be positive",
        )


async def _require_run(db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    result = await db.execute(select(AgentRun.id).where(AgentRun.tenant_id == tenant_id, AgentRun.id == run_id))
    if result.scalar_one_or_none() is None:
        raise ToolExecutionError(
            "run_not_found",
            f"run {run_id} does not exist in tenant {tenant_id}",
        )


def _execution_statement(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    lock: bool,
):
    statement = select(AgentToolExecution).where(
        AgentToolExecution.tenant_id == tenant_id,
        AgentToolExecution.run_id == run_id,
        AgentToolExecution.tool_call_id == tool_call_id,
    )
    return statement.with_for_update() if lock else statement


async def _find_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    lock: bool,
) -> AgentToolExecution | None:
    result = await db.execute(
        _execution_statement(
            tenant_id=tenant_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            lock=lock,
        )
    )
    return result.scalar_one_or_none()


async def inspect_tool_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
) -> ToolExecutionInspection:
    """Inspect one tenant/run/call ledger position without claiming execution."""
    _require_text(tool_call_id, field="tool_call_id", max_length=255)
    await _require_run(db, tenant_id=tenant_id, run_id=run_id)
    execution = await _find_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        lock=False,
    )
    if execution is None:
        return ToolExecutionInspection(status="not_started", execution=None)
    if execution.status not in _PERSISTED_STATUSES:
        raise ToolExecutionError(
            "invalid_tool_execution_state",
            f"tool execution {execution.id} has unsupported status {execution.status}",
        )
    return ToolExecutionInspection(status=execution.status, execution=execution)  # type: ignore[arg-type]


def _require_exact_request(
    existing: AgentToolExecution,
    *,
    tool_name: str,
    assistant_message_id: str,
    arguments_hash: str,
    stored_arguments: dict[str, Any],
    request_ref: str | None,
) -> None:
    expected = {
        "tool_name": tool_name,
        "assistant_message_id": assistant_message_id,
        "arguments_hash": arguments_hash,
        "sanitized_arguments": stored_arguments,
        "request_ref": request_ref,
    }
    mismatched = [field for field, value in expected.items() if getattr(existing, field) != value]
    if mismatched:
        raise ToolExecutionError(
            "tool_call_idempotency_mismatch",
            "tool_call_id already exists with different immutable inputs: " + ", ".join(sorted(mismatched)),
        )


def _outcome(execution: AgentToolExecution) -> ToolExecutionOutcome:
    return ToolExecutionOutcome(
        status=execution.status,  # type: ignore[arg-type]
        result_summary=execution.result_summary,
        result_ref=execution.result_ref,
    )


def _decision_for_existing(
    execution: AgentToolExecution,
    *,
    retry_failed: bool,
    lease_owner: str,
    lease_expires_at: datetime,
    now: datetime,
) -> ToolExecutionReservation:
    effect, retry_policy = _execution_metadata(execution)
    if execution.status == "succeeded":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=_outcome(execution),
            prior_failure=None,
            blocked=False,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code=None,
        )
    if execution.status == "started":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=True,
            reconciliation_required=True,
            requires_confirmation=False,
            error_code="tool_execution_started",
        )
    if execution.status == "unknown":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=True,
            reconciliation_required=True,
            requires_confirmation=effect != "read",
            error_code="tool_outcome_unknown",
        )
    if execution.status == "failed" and retry_failed:
        if effect != "read" or retry_policy != "safe":
            raise ToolExecutionError(
                "unsafe_tool_retry",
                "only failed read tools with retry_policy=safe may be re-reserved",
            )
        prior_failure = _outcome(execution)
        execution.status = "started"
        execution.result_summary = None
        execution.result_ref = None
        execution.lease_owner = lease_owner
        execution.lease_expires_at = lease_expires_at
        execution.started_at = now
        execution.completed_at = None
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=True,
            reusable_result=None,
            prior_failure=prior_failure,
            blocked=False,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code=None,
        )
    if execution.status == "failed":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=_outcome(execution),
            blocked=True,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code="tool_execution_failed",
        )
    raise ToolExecutionError(
        "invalid_tool_execution_state",
        f"tool execution {execution.id} has unsupported status {execution.status}",
    )


async def reserve_tool_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    assistant_message_id: str,
    arguments: dict[str, Any],
    sanitized_arguments: dict[str, Any] | None,
    request_ref: str | None,
    side_effect_classification: SideEffectClassification,
    retry_policy: RetryPolicy,
    lease_owner: str,
    lease_ttl_seconds: int,
    retry_failed: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ToolExecutionReservation:
    """Atomically reserve an exact tool call without committing the caller transaction.

    A returned reservation permits execution only when ``can_execute`` is true.
    Existing ``started`` and ``unknown`` rows are never reclaimed based on lease
    expiry; expiry makes them reconciliation candidates, not proof that an
    external side effect did not happen.
    """
    _validate_request(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        assistant_message_id=assistant_message_id,
        side_effect_classification=side_effect_classification,
        retry_policy=retry_policy,
        request_ref=request_ref,
        lease_owner=lease_owner,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    arguments_hash = fingerprint_arguments(arguments)
    stored_arguments = _stored_arguments(
        sanitized_arguments,
        side_effect_classification=side_effect_classification,
        retry_policy=retry_policy,
    )
    now = (clock or (lambda: datetime.now(UTC)))()
    lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)

    await _require_run(db, tenant_id=tenant_id, run_id=run_id)
    existing = await _find_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        lock=True,
    )
    if existing is not None:
        _require_exact_request(
            existing,
            tool_name=tool_name,
            assistant_message_id=assistant_message_id,
            arguments_hash=arguments_hash,
            stored_arguments=stored_arguments,
            request_ref=request_ref,
        )
        decision = _decision_for_existing(
            existing,
            retry_failed=retry_failed,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=now,
        )
        if decision.retrying:
            await db.flush()
        return decision

    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        assistant_message_id=assistant_message_id,
        arguments_hash=arguments_hash,
        sanitized_arguments=deepcopy(stored_arguments),
        request_ref=request_ref,
        status="started",
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        started_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(execution)
            await db.flush()
        return ToolExecutionReservation(
            execution=execution,
            created=True,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=False,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code=None,
        )
    except IntegrityError:
        concurrent = await _find_execution(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            lock=True,
        )
        if concurrent is None:
            raise
        _require_exact_request(
            concurrent,
            tool_name=tool_name,
            assistant_message_id=assistant_message_id,
            arguments_hash=arguments_hash,
            stored_arguments=stored_arguments,
            request_ref=request_ref,
        )
        # A concurrent winner has already crossed into started.  Even when its
        # lease later expires, the losing worker may not execute the call.
        return _decision_for_existing(
            concurrent,
            retry_failed=False,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=now,
        )


async def _get_locked_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
) -> AgentToolExecution:
    result = await db.execute(
        select(AgentToolExecution)
        .where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.id == execution_id,
        )
        .with_for_update()
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise ToolExecutionError(
            "tool_execution_not_found",
            f"tool execution {execution_id} does not exist in tenant {tenant_id}",
        )
    return execution


def _require_lease_owner(execution: AgentToolExecution, lease_owner: str) -> None:
    _require_text(lease_owner, field="lease_owner", max_length=128)
    if execution.status != "started" or execution.lease_owner != lease_owner:
        raise ToolExecutionError(
            "tool_execution_lease_lost",
            "tool execution is not currently started by this worker",
        )


async def renew_tool_execution_lease(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    lease_ttl_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Renew the current owner's reservation without enabling another executor."""
    if lease_ttl_seconds <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "lease_ttl_seconds must be positive",
        )
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    _require_lease_owner(execution, lease_owner)
    now = (clock or (lambda: datetime.now(UTC)))()
    execution.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    await db.flush()
    return execution


async def _mark_terminal(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    status: Literal["succeeded", "failed", "unknown"],
    result_summary: str | None,
    result_ref: str | None,
    clock: Callable[[], datetime] | None,
) -> AgentToolExecution:
    _require_optional_text(result_ref, field="result_ref", max_length=500)
    if result_summary is not None and len(result_summary) > 1_000_000:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "result_summary exceeds its storage limit",
        )
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    if execution.status == status:
        if execution.result_summary == result_summary and execution.result_ref == result_ref:
            return execution
        raise ToolExecutionError(
            "tool_execution_terminal_conflict",
            "terminal tool execution retry has different outcome data",
        )
    if execution.status in {"succeeded", "failed", "unknown"}:
        raise ToolExecutionError(
            "tool_execution_terminal_conflict",
            f"tool execution is already terminal with status {execution.status}",
        )
    _require_lease_owner(execution, lease_owner)
    execution.status = status
    execution.result_summary = result_summary
    execution.result_ref = result_ref
    execution.lease_expires_at = None
    execution.completed_at = (clock or (lambda: datetime.now(UTC)))()
    await db.flush()
    return execution


async def mark_tool_execution_succeeded(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist a reusable successful receipt under a row lock."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="succeeded",
        result_summary=result_summary,
        result_ref=result_ref,
        clock=clock,
    )


async def mark_tool_execution_failed(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist a known failure; only explicit safe-read policy may retry it."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="failed",
        result_summary=result_summary,
        result_ref=result_ref,
        clock=clock,
    )


async def mark_tool_execution_unknown(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist an uncertain outcome that always requires reconciliation."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="unknown",
        result_summary=result_summary,
        result_ref=result_ref,
        clock=clock,
    )
