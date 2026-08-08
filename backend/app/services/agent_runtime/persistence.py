"""Caller-transaction persistence for the Runtime registry and command inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
import uuid

from sqlalchemy import and_, exists, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_run_event import AgentRunEvent


_SOURCE_TYPES = frozenset({"chat", "trigger", "task", "a2a", "heartbeat"})
_RUN_KINDS = frozenset({"foreground", "background", "delegated", "orchestration"})
_RUNTIME_TYPES = frozenset({"legacy", "langgraph"})
_DELIVERY_STATUSES = frozenset({"not_required", "pending", "delivered", "failed"})


class RuntimePersistenceError(RuntimeError):
    """A stable persistence contract was rejected before changing Runtime state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RunRegistration:
    """Immutable inputs used to create one product-owned Run registry row."""

    tenant_id: uuid.UUID
    source_type: str
    goal: str
    run_kind: str
    runtime_type: str
    graph_name: str
    graph_version: str
    delivery_status: str
    agent_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    source_id: str | None = None
    source_execution_id: str | None = None
    correlation_id: str | None = None
    origin_user_id: uuid.UUID | None = None
    origin_agent_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    root_run_id: uuid.UUID | None = None
    system_role: str | None = None
    model_id: uuid.UUID | None = None
    model_turn_limit: int | None = None
    runtime_thread_id: str | None = None
    scheduling_lane_key: str | None = None
    scheduling_position_created_at: datetime | None = None
    scheduling_position_id: uuid.UUID | None = None
    delivery_target: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RegisteredRun:
    """A Run and its durable start command, whether newly created or replayed."""

    run: AgentRun
    start_command: AgentRunCommand
    created: bool


@dataclass(frozen=True, slots=True)
class EnqueuedCommand:
    """A durable command, whether newly inserted or an exact idempotent retry."""

    command: AgentRunCommand
    created: bool


def _require_text(value: str, *, field: str, max_length: int) -> None:
    if not value or not value.strip():
        raise RuntimePersistenceError("invalid_runtime_input", f"{field} must not be blank")
    if len(value) > max_length:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            f"{field} exceeds its {max_length}-character storage limit",
        )


def _require_optional_text(value: str | None, *, field: str, max_length: int) -> None:
    if value is None:
        return
    _require_text(value, field=field, max_length=max_length)


def _validate_registration(registration: RunRegistration) -> None:
    if registration.source_type not in _SOURCE_TYPES:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            f"unsupported source_type: {registration.source_type}",
        )
    if registration.run_kind not in _RUN_KINDS:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            f"unsupported run_kind: {registration.run_kind}",
        )
    if registration.runtime_type not in _RUNTIME_TYPES:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            f"unsupported runtime_type: {registration.runtime_type}",
        )
    if registration.delivery_status not in _DELIVERY_STATUSES:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            f"unsupported delivery_status: {registration.delivery_status}",
        )

    _require_text(registration.goal, field="goal", max_length=1_000_000)
    _require_text(registration.graph_name, field="graph_name", max_length=100)
    _require_text(registration.graph_version, field="graph_version", max_length=64)
    _require_optional_text(registration.source_id, field="source_id", max_length=200)
    _require_optional_text(
        registration.source_execution_id,
        field="source_execution_id",
        max_length=200,
    )
    _require_optional_text(registration.correlation_id, field="correlation_id", max_length=200)
    _require_optional_text(
        registration.runtime_thread_id,
        field="runtime_thread_id",
        max_length=255,
    )
    _require_optional_text(
        registration.scheduling_lane_key,
        field="scheduling_lane_key",
        max_length=255,
    )

    if registration.runtime_type == "langgraph" and registration.model_id is None:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            "langgraph runs must pin model_id at creation",
        )
    if registration.run_kind == "orchestration":
        if (
            registration.agent_id is not None
            or registration.system_role != "group_planning"
            or registration.model_id is None
            or registration.model_turn_limit is not None
        ):
            raise RuntimePersistenceError(
                "invalid_runtime_input",
                "orchestration runs require agent_id=null, system_role=group_planning, model_id, and no Agent turn limit",
            )
    else:
        if registration.agent_id is None or registration.system_role is not None:
            raise RuntimePersistenceError(
                "invalid_runtime_input",
                "non-orchestration runs require agent_id and system_role=null",
            )
        if (
            isinstance(registration.model_turn_limit, bool)
            or not isinstance(registration.model_turn_limit, int)
            or registration.model_turn_limit <= 0
        ):
            raise RuntimePersistenceError(
                "invalid_runtime_input",
                "non-orchestration runs require a positive model_turn_limit",
            )

    lane_values = (
        registration.scheduling_lane_key,
        registration.scheduling_position_created_at,
        registration.scheduling_position_id,
    )
    if any(value is None for value in lane_values) and any(value is not None for value in lane_values):
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            "scheduling lane key and both position fields must be provided together",
        )
    if registration.delivery_target is not None and not isinstance(registration.delivery_target, dict):
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            "delivery_target must be an object when provided",
        )


def _validate_command_input(idempotency_key: str, payload: dict[str, Any]) -> None:
    _require_text(idempotency_key, field="idempotency_key", max_length=255)
    if not isinstance(payload, dict):
        raise RuntimePersistenceError("invalid_runtime_input", "command payload must be an object")


def _registration_values(registration: RunRegistration) -> dict[str, Any]:
    return {
        "tenant_id": registration.tenant_id,
        "agent_id": registration.agent_id,
        "session_id": registration.session_id,
        "source_type": registration.source_type,
        "source_id": registration.source_id,
        "source_execution_id": registration.source_execution_id,
        "correlation_id": registration.correlation_id,
        "origin_user_id": registration.origin_user_id,
        "origin_agent_id": registration.origin_agent_id,
        "parent_run_id": registration.parent_run_id,
        "root_run_id": registration.root_run_id,
        "goal": registration.goal,
        "run_kind": registration.run_kind,
        "system_role": registration.system_role,
        "model_id": registration.model_id,
        "model_turn_limit": registration.model_turn_limit,
        "runtime_type": registration.runtime_type,
        "graph_name": registration.graph_name,
        "graph_version": registration.graph_version,
        "scheduling_lane_key": registration.scheduling_lane_key,
        "scheduling_position_created_at": registration.scheduling_position_created_at,
        "scheduling_position_id": registration.scheduling_position_id,
        "delivery_target": registration.delivery_target,
    }


def _require_exact_source_retry(existing: AgentRun, registration: RunRegistration) -> None:
    mismatched = [
        field for field, expected in _registration_values(registration).items() if getattr(existing, field) != expected
    ]
    expected_thread_id = registration.runtime_thread_id or str(existing.id)
    if existing.runtime_thread_id != expected_thread_id:
        mismatched.append("runtime_thread_id")
    if mismatched:
        raise RuntimePersistenceError(
            "source_idempotency_mismatch",
            "source execution already exists with different immutable inputs: " + ", ".join(sorted(mismatched)),
        )


async def _find_source_run(
    db: AsyncSession,
    *,
    source_type: str,
    source_execution_id: str,
) -> AgentRun | None:
    result = await db.execute(
        select(AgentRun).where(
            AgentRun.source_type == source_type,
            AgentRun.source_execution_id == source_execution_id,
        )
    )
    return result.scalar_one_or_none()


async def _resolve_source_retry(
    db: AsyncSession,
    registration: RunRegistration,
    *,
    start_payload: dict[str, Any],
    start_idempotency_key: str,
    actor_user_id: uuid.UUID | None,
    actor_agent_id: uuid.UUID | None,
) -> RegisteredRun | None:
    if registration.source_execution_id is None:
        return None

    existing = await _find_source_run(
        db,
        source_type=registration.source_type,
        source_execution_id=registration.source_execution_id,
    )
    if existing is None:
        return None

    _require_exact_source_retry(existing, registration)
    command_result = await db.execute(
        select(AgentRunCommand).where(
            AgentRunCommand.run_id == existing.id,
            AgentRunCommand.idempotency_key == start_idempotency_key,
        )
    )
    start_command = command_result.scalar_one_or_none()
    if start_command is None:
        raise RuntimePersistenceError(
            "source_retry_missing_start_command",
            "source execution exists without its expected start command",
        )
    _require_exact_command_retry(
        start_command,
        tenant_id=registration.tenant_id,
        command_type="start",
        payload=start_payload,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
    )
    return RegisteredRun(run=existing, start_command=start_command, created=False)


def _require_exact_command_retry(
    existing: AgentRunCommand,
    *,
    tenant_id: uuid.UUID,
    command_type: str,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None,
    actor_agent_id: uuid.UUID | None,
) -> None:
    expected = {
        "tenant_id": tenant_id,
        "command_type": command_type,
        "payload": payload,
        "actor_user_id": actor_user_id,
        "actor_agent_id": actor_agent_id,
    }
    mismatched = [field for field, value in expected.items() if getattr(existing, field) != value]
    if mismatched:
        raise RuntimePersistenceError(
            "command_idempotency_mismatch",
            "command idempotency key already exists with different inputs: " + ", ".join(sorted(mismatched)),
        )


async def register_run_with_start(
    db: AsyncSession,
    registration: RunRegistration,
    *,
    start_payload: dict[str, Any],
    start_idempotency_key: str,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
) -> RegisteredRun:
    """Register a Run and start command in the caller's current transaction."""
    _validate_registration(registration)
    _validate_command_input(start_idempotency_key, start_payload)
    payload = dict(start_payload)

    existing = await _resolve_source_retry(
        db,
        registration,
        start_payload=payload,
        start_idempotency_key=start_idempotency_key,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
    )
    if existing is not None:
        return existing

    run_id = uuid.uuid4()
    run = AgentRun(
        id=run_id,
        runtime_thread_id=registration.runtime_thread_id or str(run_id),
        lane_held=False,
        delivery_status=registration.delivery_status,
        **_registration_values(registration),
    )
    start_command = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=registration.tenant_id,
        run_id=run_id,
        command_type="start",
        payload=payload,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        idempotency_key=start_idempotency_key,
        status="pending",
        attempt_count=0,
    )
    created_event = AgentRunEvent(
        id=uuid.uuid5(run_id, "lifecycle-event:run_created"),
        tenant_id=registration.tenant_id,
        run_id=run_id,
        agent_id=registration.agent_id,
        event_type="run_created",
        summary="Runtime Run created",
        payload={
            "status": "queued",
            "source_type": registration.source_type,
            "thread_id": run.runtime_thread_id,
        },
        artifact_refs=[],
        idempotency_key=f"run:{run_id}:created",
        source_checkpoint_id=None,
    )
    try:
        async with db.begin_nested():
            db.add(run)
            db.add(start_command)
            db.add(created_event)
            await db.flush()
        return RegisteredRun(run=run, start_command=start_command, created=True)
    except IntegrityError:
        concurrent = await _resolve_source_retry(
            db,
            registration,
            start_payload=payload,
            start_idempotency_key=start_idempotency_key,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
        )
        if concurrent is None:
            raise
        return concurrent


async def _enqueue_command(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    command_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    actor_user_id: uuid.UUID | None,
    actor_agent_id: uuid.UUID | None,
) -> EnqueuedCommand:
    _validate_command_input(idempotency_key, payload)
    payload_copy = dict(payload)

    run_result = await db.execute(select(AgentRun).where(AgentRun.tenant_id == tenant_id, AgentRun.id == run_id))
    if run_result.scalar_one_or_none() is None:
        raise RuntimePersistenceError("run_not_found", f"run {run_id} does not exist in tenant {tenant_id}")

    command_result = await db.execute(
        select(AgentRunCommand).where(
            AgentRunCommand.run_id == run_id,
            AgentRunCommand.idempotency_key == idempotency_key,
        )
    )
    existing = command_result.scalar_one_or_none()
    if existing is not None:
        _require_exact_command_retry(
            existing,
            tenant_id=tenant_id,
            command_type=command_type,
            payload=payload_copy,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
        )
        return EnqueuedCommand(command=existing, created=False)

    command = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        command_type=command_type,
        payload=payload_copy,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        idempotency_key=idempotency_key,
        status="pending",
        attempt_count=0,
    )
    try:
        async with db.begin_nested():
            db.add(command)
            await db.flush()
        return EnqueuedCommand(command=command, created=True)
    except IntegrityError:
        concurrent_result = await db.execute(
            select(AgentRunCommand).where(
                AgentRunCommand.run_id == run_id,
                AgentRunCommand.idempotency_key == idempotency_key,
            )
        )
        concurrent = concurrent_result.scalar_one_or_none()
        if concurrent is None:
            raise
        _require_exact_command_retry(
            concurrent,
            tenant_id=tenant_id,
            command_type=command_type,
            payload=payload_copy,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
        )
        return EnqueuedCommand(command=concurrent, created=False)


async def enqueue_resume(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: dict[str, Any],
    idempotency_key: str,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
) -> EnqueuedCommand:
    """Persist one idempotent resume command without committing the caller transaction."""
    return await _enqueue_command(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        command_type="resume",
        payload=payload,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
    )


async def enqueue_cancel(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    idempotency_key: str,
    reason: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
) -> EnqueuedCommand:
    """Persist one idempotent cooperative cancel command."""
    payload = {"reason": reason} if reason is not None else {}
    return await _enqueue_command(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        command_type="cancel",
        payload=payload,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
    )


def _claim_statement(now: datetime, *, max_attempts: int):
    previous = aliased(AgentRunCommand, name="previous_command")
    candidate_run = aliased(AgentRun, name="candidate_run")
    lane_holder = aliased(AgentRun, name="lane_holder")
    earlier_lane_run = aliased(AgentRun, name="earlier_lane_run")
    earlier_lane_command = aliased(AgentRunCommand, name="earlier_lane_command")
    earlier_unfinished = exists(
        select(1).where(
            previous.run_id == AgentRunCommand.run_id,
            previous.status.in_(("pending", "claimed")),
            tuple_(previous.created_at, previous.id) < tuple_(AgentRunCommand.created_at, AgentRunCommand.id),
            or_(
                AgentRunCommand.command_type != "cancel",
                previous.command_type != "start",
                and_(
                    previous.status == "claimed",
                    previous.claim_expires_at >= now,
                ),
            ),
        )
    )
    active_lane_holder = exists(
        select(1).where(
            lane_holder.scheduling_lane_key == candidate_run.scheduling_lane_key,
            lane_holder.id != candidate_run.id,
            lane_holder.lane_held.is_(True),
        )
    )
    earlier_lane_start = exists(
        select(1)
        .select_from(earlier_lane_command)
        .join(earlier_lane_run, earlier_lane_run.id == earlier_lane_command.run_id)
        .where(
            earlier_lane_run.scheduling_lane_key == candidate_run.scheduling_lane_key,
            earlier_lane_command.command_type == "start",
            earlier_lane_command.status.in_(("pending", "claimed")),
            tuple_(
                earlier_lane_run.scheduling_position_created_at,
                earlier_lane_run.scheduling_position_id,
                earlier_lane_run.created_at,
                earlier_lane_run.id,
            )
            < tuple_(
                candidate_run.scheduling_position_created_at,
                candidate_run.scheduling_position_id,
                candidate_run.created_at,
                candidate_run.id,
            ),
        )
    )
    return (
        select(AgentRunCommand)
        .join(candidate_run, candidate_run.id == AgentRunCommand.run_id)
        .where(
            or_(
                AgentRunCommand.status == "pending",
                and_(
                    AgentRunCommand.status == "claimed",
                    AgentRunCommand.claim_expires_at < now,
                ),
            ),
            ~earlier_unfinished,
            or_(
                candidate_run.scheduling_lane_key.is_(None),
                AgentRunCommand.command_type != "start",
                candidate_run.lane_held.is_(True),
                and_(~active_lane_holder, ~earlier_lane_start),
            ),
        )
        .order_by(AgentRunCommand.created_at, AgentRunCommand.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )


async def _acquire_start_lane(
    db: AsyncSession,
    *,
    command: AgentRunCommand,
    now: datetime,
) -> bool:
    """Claim one queued mention lane without consulting lifecycle projections."""
    run_result = await db.execute(
        select(AgentRun).where(AgentRun.id == command.run_id).with_for_update()
    )
    run = run_result.scalar_one_or_none()
    if run is None or run.tenant_id != command.tenant_id:
        raise RuntimePersistenceError(
            "run_not_found",
            "start command Run does not exist in its tenant",
        )
    if run.scheduling_lane_key is None or run.lane_held:
        return True

    holder_result = await db.execute(
        select(AgentRun.id)
        .where(
            AgentRun.scheduling_lane_key == run.scheduling_lane_key,
            AgentRun.id != run.id,
            AgentRun.lane_held.is_(True),
        )
        .limit(1)
        .with_for_update()
    )
    if holder_result.scalar_one_or_none() is not None:
        return False
    run.lane_held = True
    run.lane_claimed_at = now
    return True


async def claim_next_command(
    db: AsyncSession,
    *,
    claimant: str,
    claim_ttl_seconds: int,
    max_attempts: int,
    clock: Callable[[], datetime] | None = None,
) -> AgentRunCommand | None:
    """Claim the oldest eligible command while preserving per-Run input order."""
    _require_text(claimant, field="claimant", max_length=128)
    if claim_ttl_seconds <= 0:
        raise RuntimePersistenceError("invalid_runtime_input", "claim_ttl_seconds must be positive")
    if max_attempts <= 0:
        raise RuntimePersistenceError("invalid_runtime_input", "max_attempts must be positive")
    now_fn = clock or (lambda: datetime.now(UTC))

    now = now_fn()
    result = await db.execute(_claim_statement(now, max_attempts=max_attempts))
    command = result.scalar_one_or_none()
    if command is None:
        return None
    if (
        command.attempt_count < max_attempts
        and command.command_type == "start"
        and not await _acquire_start_lane(
            db,
            command=command,
            now=now,
        )
    ):
        return None

    command.claimed_by = claimant
    command.status = "claimed"
    command.claim_expires_at = now + timedelta(seconds=claim_ttl_seconds)
    command.error_code = None
    command.applied_at = None
    await db.flush()
    return command


async def mark_command_product_synced(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
) -> AgentRunCommand:
    """Clear the post-settlement reconciliation marker idempotently."""
    command = await _get_locked_command(db, tenant_id=tenant_id, command_id=command_id)
    if command.status != "applied":
        raise RuntimePersistenceError(
            "command_not_applied",
            "product synchronization requires an applied Command",
        )
    if command.error_code is None:
        return command
    if command.error_code != "product_sync_pending":
        raise RuntimePersistenceError(
            "command_reconciliation_conflict",
            "applied Command has an unrelated error marker",
        )
    command.error_code = None
    await db.flush()
    return command


async def _get_locked_command(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
) -> AgentRunCommand:
    result = await db.execute(
        select(AgentRunCommand)
        .where(
            AgentRunCommand.tenant_id == tenant_id,
            AgentRunCommand.id == command_id,
        )
        .with_for_update()
    )
    command = result.scalar_one_or_none()
    if command is None:
        raise RuntimePersistenceError(
            "command_not_found",
            f"command {command_id} does not exist in tenant {tenant_id}",
        )
    return command


async def begin_command_attempt(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
    claimant: str,
    max_attempts: int,
) -> AgentRunCommand:
    """Consume one business recovery attempt after the Thread lock is held."""
    if max_attempts <= 0:
        raise RuntimePersistenceError(
            "invalid_runtime_input",
            "max_attempts must be positive",
        )
    command = await _get_locked_command(
        db,
        tenant_id=tenant_id,
        command_id=command_id,
    )
    _require_claimant(command, claimant)
    if command.attempt_count >= max_attempts:
        raise RuntimePersistenceError(
            "command_reconciliation_required",
            "command reached its attempt limit and must be quarantined",
        )
    command.attempt_count += 1
    await db.flush()
    return command


def _require_claimant(command: AgentRunCommand, claimant: str) -> None:
    _require_text(claimant, field="claimant", max_length=128)
    if command.status != "claimed" or command.claimed_by != claimant:
        raise RuntimePersistenceError(
            "command_claim_lost",
            "command is not currently claimed by this worker",
        )


async def mark_command_applied(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
    claimant: str,
    applied_checkpoint_id: str | None,
    clock: Callable[[], datetime] | None = None,
) -> AgentRunCommand:
    """Mark a claimed command applied after its checkpoint is observable."""
    command = await _get_locked_command(db, tenant_id=tenant_id, command_id=command_id)
    if applied_checkpoint_id is None:
        if command.command_type != "cancel":
            raise RuntimePersistenceError(
                "invalid_runtime_input",
                "only cancel-before-start may settle without a checkpoint",
            )
    else:
        _require_text(applied_checkpoint_id, field="applied_checkpoint_id", max_length=255)
    if (
        command.status == "applied"
        and command.claimed_by == claimant
        and command.applied_checkpoint_id == applied_checkpoint_id
    ):
        return command
    _require_claimant(command, claimant)
    command.status = "applied"
    command.applied_checkpoint_id = applied_checkpoint_id
    # Graph/control settlement and product reconciliation are intentionally
    # separate. This existing field is a narrow crash-safe receipt so a
    # reconciler can retry products without returning the Command to pending.
    command.error_code = "product_sync_pending"
    command.claim_expires_at = None
    command.applied_at = (clock or (lambda: datetime.now(UTC)))()
    await db.flush()
    return command


async def reject_unstarted_run_for_cancel(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    cancel_command_id: uuid.UUID,
    clock: Callable[[], datetime] | None = None,
) -> tuple[AgentRunCommand, ...]:
    """Reject prior start work only when cancel observes no Run checkpoint.

    A currently claimed start is never preempted here; the Thread lock makes
    this path reachable only for pending or expired claims.
    """
    cancel = await _get_locked_command(
        db,
        tenant_id=tenant_id,
        command_id=cancel_command_id,
    )
    if cancel.run_id != run_id or cancel.command_type != "cancel":
        raise RuntimePersistenceError(
            "command_scope_mismatch",
            "cancel Command does not belong to the target Run",
        )
    now = (clock or (lambda: datetime.now(UTC)))()
    result = await db.execute(
        select(AgentRunCommand)
        .where(
            AgentRunCommand.tenant_id == tenant_id,
            AgentRunCommand.run_id == run_id,
            AgentRunCommand.command_type == "start",
            tuple_(AgentRunCommand.created_at, AgentRunCommand.id)
            < tuple_(cancel.created_at, cancel.id),
            or_(
                AgentRunCommand.status == "pending",
                and_(
                    AgentRunCommand.status == "claimed",
                    AgentRunCommand.claim_expires_at < now,
                ),
            ),
        )
        .with_for_update()
    )
    starts = tuple(result.scalars().all())
    for start in starts:
        start.status = "rejected"
        start.claimed_by = None
        start.claim_expires_at = None
        start.applied_checkpoint_id = None
        start.error_code = "cancelled_before_start"
        start.applied_at = now
    if starts:
        await db.flush()
    return starts


async def mark_command_rejected(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
    claimant: str,
    error_code: str,
    clock: Callable[[], datetime] | None = None,
) -> AgentRunCommand:
    """Reject a claimed command and release an abandoned start lane atomically."""
    _require_text(error_code, field="error_code", max_length=100)
    command = await _get_locked_command(db, tenant_id=tenant_id, command_id=command_id)
    already_rejected = (
        command.status == "rejected"
        and command.claimed_by == claimant
        and command.error_code == error_code
    )
    if not already_rejected:
        _require_claimant(command, claimant)
        command.status = "rejected"
        command.applied_checkpoint_id = None
        command.error_code = error_code
        command.claim_expires_at = None
        command.applied_at = (clock or (lambda: datetime.now(UTC)))()

    lane_released = False
    if command.command_type == "start":
        run_result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.id == command.run_id,
            )
            .with_for_update()
        )
        run = run_result.scalar_one_or_none()
        if run is None:
            raise RuntimePersistenceError(
                "run_not_found",
                "rejected start command Run does not exist in its tenant",
            )
        if run.lane_held:
            run.lane_held = False
            run.lane_claimed_at = None
            lane_released = True

    if not already_rejected or lane_released:
        await db.flush()
    return command


def _release_rejected_start_lanes_statement():
    rejected_start = exists(
        select(AgentRunCommand.id).where(
            AgentRunCommand.tenant_id == AgentRun.tenant_id,
            AgentRunCommand.run_id == AgentRun.id,
            AgentRunCommand.command_type == "start",
            AgentRunCommand.status == "rejected",
        )
    )
    return (
        update(AgentRun)
        .where(
            AgentRun.lane_held.is_(True),
            rejected_start,
        )
        .values(
            lane_held=False,
            lane_claimed_at=None,
        )
    )


async def release_rejected_start_lanes(db: AsyncSession) -> int:
    """Repair lanes left behind by start rejections from older workers."""
    result = await db.execute(_release_rejected_start_lanes_statement())
    return max(result.rowcount or 0, 0)


async def renew_command_claim(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
    claimant: str,
    claim_ttl_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> AgentRunCommand:
    """Extend one active claim from a short, independently committed transaction."""
    if claim_ttl_seconds <= 0:
        raise RuntimePersistenceError("invalid_runtime_input", "claim_ttl_seconds must be positive")
    command = await _get_locked_command(db, tenant_id=tenant_id, command_id=command_id)
    _require_claimant(command, claimant)
    command.claim_expires_at = (clock or (lambda: datetime.now(UTC)))() + timedelta(seconds=claim_ttl_seconds)
    await db.flush()
    return command


async def release_command_claim(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command_id: uuid.UUID,
    claimant: str,
    error_code: str,
) -> AgentRunCommand:
    """Return retryable work to pending without changing its attempt counter."""
    _require_text(error_code, field="error_code", max_length=100)
    command = await _get_locked_command(db, tenant_id=tenant_id, command_id=command_id)
    _require_claimant(command, claimant)
    command.status = "pending"
    command.claimed_by = None
    command.claim_expires_at = None
    command.applied_checkpoint_id = None
    command.error_code = error_code
    command.applied_at = None
    await db.flush()
    return command
