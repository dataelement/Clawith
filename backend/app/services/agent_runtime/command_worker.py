"""Reliable Command Inbox orchestration around one authoritative checkpoint."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import logging
from typing import Literal, Protocol, cast
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.config import Settings, get_settings
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.persistence import (
    begin_command_attempt,
    claim_next_command,
    mark_command_applied,
    mark_command_product_synced,
    mark_command_rejected,
    reject_unstarted_run_for_cancel,
    release_command_claim,
    renew_command_claim,
)
from app.services.agent_runtime.node_executor import RuntimeInvocationCancelled
from app.services.agent_runtime.state import (
    JsonObject,
    RuntimeGraphState,
)
from app.services.agent_runtime.thread_lock import ThreadLockNotAcquired, run_with_thread_lock
from app.services.agent_runtime.tool_execution import (
    ToolExecutionReconciliationPending,
)


logger = logging.getLogger(__name__)

RuntimeCommandType = Literal["start", "resume", "cancel"]
CommandWorkStatus = Literal["idle", "applied", "reconciled", "rejected", "retry"]
CheckpointDisposition = Literal[
    "not_started",
    "runnable",
    "execution_error_recoverable",
    "waiting",
    "terminal",
    "inconsistent",
]
_COMMAND_TYPES = frozenset({"start", "resume", "cancel"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_LIFECYCLE_STATUSES = frozenset(
    {
        "created",
        "queued",
        "running",
        "waiting_user",
        "waiting_external",
        "waiting_agent",
        "verifying",
        *_TERMINAL_STATUSES,
    }
)


class RuntimeSessionFactory(Protocol):
    """Create one short-lived product database session."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


@dataclass(frozen=True, slots=True)
class RuntimeRunRecord:
    """Execution-safe Run identity; no product projection or ORM state."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    thread_id: str
    runtime_type: str
    goal: str
    run_kind: str
    source_type: str
    model_id: str
    graph_name: str
    graph_version: str
    agent_id: str | None = None
    session_id: str | None = None
    system_role: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None
    model_turn_limit: int | None = None
    source_id: str | None = None
    scheduling_position_created_at: datetime | None = None
    scheduling_position_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCommandRecord:
    """Detached command input retained after the short claim transaction."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    command_type: RuntimeCommandType
    payload: JsonObject
    actor_user_id: uuid.UUID | None
    actor_agent_id: uuid.UUID | None
    attempt_count: int = 0


@dataclass(frozen=True, slots=True)
class CheckpointObservation:
    """Complete state needed to classify one committed ``StateSnapshot``."""

    checkpoint_id: str
    state: RuntimeGraphState
    next_nodes: tuple[str, ...] = ()
    tasks: tuple[object, ...] = ()
    interrupts: tuple[object, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime | None = None


def classify_checkpoint(
    observation: CheckpointObservation | None,
) -> CheckpointDisposition:
    """Classify execution only when values/next/tasks/interrupts agree."""
    if observation is None:
        return "not_started"
    try:
        status = observation.state["lifecycle"]["status"]
    except (KeyError, TypeError):
        return "inconsistent"

    task_names = tuple(
        str(name)
        for task in observation.tasks
        if (name := getattr(task, "name", None)) is not None
    )
    if task_names and task_names != observation.next_nodes:
        return "inconsistent"

    if status in _TERMINAL_STATUSES:
        if observation.next_nodes or observation.tasks or observation.interrupts:
            return "inconsistent"
        return "terminal"

    if status in {"waiting_user", "waiting_external", "waiting_agent"}:
        if not observation.next_nodes or not observation.tasks or not observation.interrupts:
            return "inconsistent"
        return "waiting"

    if observation.interrupts or not observation.next_nodes or not observation.tasks:
        return "inconsistent"
    if any(getattr(task, "error", None) is not None for task in observation.tasks):
        return "execution_error_recoverable"
    return "runnable"


class RuntimeCheckpointReader(Protocol):
    """Read exact Run/Command checkpoints without trusting Thread latest."""

    async def read_for_command(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
    ) -> CheckpointObservation | None: ...

    async def read_latest(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
    ) -> CheckpointObservation | None: ...


class RuntimeCommandExecutor(Protocol):
    """Apply one validated command through a versioned LangGraph driver."""

    async def execute(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None: ...


class RuntimePostCheckpointHandler(Protocol):
    """Apply idempotent products after Graph/control settlement."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None: ...


class RuntimePreCommandHandler(Protocol):
    """Apply idempotent product work after intake commit and before Graph execution."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None: ...


class CommandWorkerError(RuntimeError):
    """Command processing failed with a stable, non-sensitive code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RetryableCommandError(CommandWorkerError):
    """Command remains safe to claim again after checkpoint reconciliation."""


class CommandExecutionRejected(CommandWorkerError):
    """A driver deterministically rejected the command without advancing state."""


class CommandCheckpointNotObserved(RetryableCommandError):
    """Graph returned without an observable checkpoint containing this command."""

    def __init__(self, command_id: uuid.UUID) -> None:
        super().__init__(
            "checkpoint_not_observed",
            f"checkpoint containing command {command_id} was not observable",
        )


@dataclass(frozen=True, slots=True)
class CommandWorkResult:
    """One bounded worker iteration result for daemon metrics and retry policy."""

    status: CommandWorkStatus
    command_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    checkpoint_id: str | None = None
    error_code: str | None = None


def runtime_command_record(command: AgentRunCommand) -> RuntimeCommandRecord:
    payload = command.payload
    if not isinstance(payload, dict):
        raise RetryableCommandError(
            "invalid_command_payload",
            "persisted command payload is not an object",
        )
    return RuntimeCommandRecord(
        id=command.id,
        tenant_id=command.tenant_id,
        run_id=command.run_id,
        command_type=cast(RuntimeCommandType, command.command_type),
        payload=dict(payload),
        actor_user_id=command.actor_user_id,
        actor_agent_id=command.actor_agent_id,
        attempt_count=command.attempt_count,
    )


class RuntimeCommandWorker:
    """Claim, reconcile, execute, and settle one Runtime command at a time."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        lock_engine: AsyncEngine,
        checkpoint_reader: RuntimeCheckpointReader,
        command_executor: RuntimeCommandExecutor,
        post_checkpoint_handler: RuntimePostCheckpointHandler,
        pre_command_handler: RuntimePreCommandHandler | None = None,
        claimant: str,
        settings: Settings | None = None,
        claim_ttl_seconds: int | None = None,
        claim_renew_seconds: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        runtime_settings = settings or get_settings()
        self._session_factory = session_factory
        self._lock_engine = lock_engine
        self._checkpoint_reader = checkpoint_reader
        self._command_executor = command_executor
        self._pre_command_handler = pre_command_handler
        self._post_checkpoint_handler = post_checkpoint_handler
        self._claimant = claimant
        self._claim_ttl_seconds = (
            claim_ttl_seconds
            if claim_ttl_seconds is not None
            else runtime_settings.AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS
        )
        self._claim_renew_seconds = (
            claim_renew_seconds
            if claim_renew_seconds is not None
            else runtime_settings.AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS
        )
        self._max_attempts = (
            max_attempts if max_attempts is not None else runtime_settings.AGENT_RUNTIME_COMMAND_MAX_ATTEMPTS
        )
        if not claimant.strip():
            raise ValueError("claimant must not be blank")
        if self._claim_ttl_seconds <= 0 or self._claim_renew_seconds <= 0:
            raise ValueError("claim TTL and renewal interval must be positive")
        if self._claim_renew_seconds >= self._claim_ttl_seconds:
            raise ValueError("claim renewal interval must be less than claim TTL")
        if self._max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

    async def _claim(self) -> RuntimeCommandRecord | None:
        async with self._session_factory() as db:
            async with db.begin():
                command = await claim_next_command(
                    db,
                    claimant=self._claimant,
                    claim_ttl_seconds=self._claim_ttl_seconds,
                    max_attempts=self._max_attempts,
                )
                if command is None:
                    return None
                return runtime_command_record(command)

    async def _load_run(self, command: RuntimeCommandRecord) -> RuntimeRunRecord:
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == command.tenant_id,
                        AgentRun.id == command.run_id,
                    )
                )
                run = result.scalar_one_or_none()
                if run is None:
                    raise CommandExecutionRejected(
                        "run_not_found",
                        "command Run does not exist in its tenant",
                    )
                if run.runtime_type != "langgraph":
                    raise CommandExecutionRejected(
                        "legacy_runtime",
                        "Runtime v2 worker cannot advance a legacy Run",
                    )
                if run.tenant_id != command.tenant_id or run.id != command.run_id:
                    raise RetryableCommandError(
                        "run_scope_mismatch",
                        "loaded Run identity does not match the claimed command",
                    )
                if not run.runtime_thread_id or not run.runtime_thread_id.strip():
                    raise RetryableCommandError(
                        "runtime_identity_mismatch",
                        "Run thread_id must not be blank",
                    )
                if run.model_id is None or not run.graph_name or not run.graph_version:
                    raise RetryableCommandError(
                        "invalid_graph_identity",
                        "LangGraph Run is missing pinned model or graph identity",
                    )
                return RuntimeRunRecord(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    thread_id=run.runtime_thread_id,
                    runtime_type=run.runtime_type,
                    goal=run.goal,
                    run_kind=run.run_kind,
                    source_type=run.source_type,
                    model_id=str(run.model_id),
                    graph_name=run.graph_name,
                    graph_version=run.graph_version,
                    agent_id=str(run.agent_id) if run.agent_id is not None else None,
                    session_id=str(run.session_id) if run.session_id is not None else None,
                    system_role=run.system_role,
                    parent_run_id=(str(run.parent_run_id) if run.parent_run_id is not None else None),
                    root_run_id=str(run.root_run_id) if run.root_run_id is not None else None,
                    model_turn_limit=run.model_turn_limit,
                    source_id=run.source_id,
                    scheduling_position_created_at=(
                        run.scheduling_position_created_at
                    ),
                    scheduling_position_id=run.scheduling_position_id,
                )

    async def _renew_claim(self, command: RuntimeCommandRecord) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await renew_command_claim(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                    claimant=self._claimant,
                    claim_ttl_seconds=self._claim_ttl_seconds,
                )

    async def _begin_attempt(self, command: RuntimeCommandRecord) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await begin_command_attempt(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                    claimant=self._claimant,
                    max_attempts=self._max_attempts,
                )

    async def _heartbeat(self, command: RuntimeCommandRecord, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._claim_renew_seconds)
                return
            except TimeoutError:
                try:
                    await self._renew_claim(command)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Runtime command claim heartbeat failed", extra={"command_id": command.id})
                    return

    async def _mark_applied(
        self,
        command: RuntimeCommandRecord,
        checkpoint_id: str | None,
    ) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await mark_command_applied(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                    claimant=self._claimant,
                    applied_checkpoint_id=checkpoint_id,
                )

    async def _reject_unstarted_start(self, command: RuntimeCommandRecord) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await reject_unstarted_run_for_cancel(
                    db,
                    tenant_id=command.tenant_id,
                    run_id=command.run_id,
                    cancel_command_id=command.id,
                )

    async def _mark_rejected(self, command: RuntimeCommandRecord, error_code: str) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await mark_command_rejected(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                    claimant=self._claimant,
                    error_code=error_code,
                )

    async def _mark_product_synced(self, command: RuntimeCommandRecord) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await mark_command_product_synced(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                )

    async def _release_for_retry(self, command: RuntimeCommandRecord, error_code: str) -> None:
        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await release_command_claim(
                        db,
                        tenant_id=command.tenant_id,
                        command_id=command.id,
                        claimant=self._claimant,
                        error_code=error_code,
                    )
        except Exception:
            # A failed release still becomes reclaimable when its existing TTL
            # expires. Do not mask the execution failure that caused the retry.
            logger.exception("Runtime command claim release failed", extra={"command_id": command.id})

    async def _defer_without_attempt(
        self,
        command: RuntimeCommandRecord,
        error_code: str,
    ) -> None:
        """Release active-owner contention and refund this business attempt."""
        async with self._session_factory() as db:
            async with db.begin():
                released = await release_command_claim(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                    claimant=self._claimant,
                    error_code=error_code,
                )
                if released.attempt_count <= 0:
                    raise CommandWorkerError(
                        "invalid_command_attempt",
                        "deferred command has no consumed attempt to refund",
                    )
                released.attempt_count -= 1
                await db.flush()

    @staticmethod
    def _validate_checkpoint(
        run: RuntimeRunRecord,
        observation: CheckpointObservation,
        *,
        command: RuntimeCommandRecord | None = None,
    ) -> None:
        if not observation.checkpoint_id.strip():
            raise RetryableCommandError(
                "invalid_checkpoint_id",
                "checkpoint reader returned a blank checkpoint ID",
            )
        try:
            lifecycle = observation.state["lifecycle"]
            status = lifecycle["status"]
        except (KeyError, TypeError) as exc:
            raise RetryableCommandError(
                "invalid_checkpoint_state",
                "checkpoint is missing Runtime lifecycle state",
            ) from exc
        if status not in _LIFECYCLE_STATUSES:
            raise RetryableCommandError(
                "invalid_checkpoint_status",
                "checkpoint lifecycle status is unsupported",
            )
        if observation.metadata.get("clawith_run_id") != str(run.run_id):
            raise RetryableCommandError(
                "checkpoint_identity_mismatch",
                "checkpoint metadata does not belong to the locked Run",
            )
        if (
            command is not None
            and observation.metadata.get("clawith_command_id") != str(command.id)
        ):
            raise RetryableCommandError(
                "checkpoint_command_mismatch",
                "checkpoint metadata does not belong to the claimed Command",
            )

    async def _reject(
        self,
        command: RuntimeCommandRecord,
        error_code: str,
    ) -> CommandWorkResult:
        await self._mark_rejected(command, error_code)
        return CommandWorkResult(
            status="rejected",
            command_id=command.id,
            run_id=command.run_id,
            error_code=error_code,
        )

    async def _sync_products_best_effort(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        try:
            await self._post_checkpoint_handler.handle(
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
            await self._mark_product_synced(command)
        except Exception:
            # Product reconciliation is deliberately downstream from the
            # durable Graph/control boundary. Retrying the Command here could
            # re-enter the Graph and repeat model/tool side effects.
            logger.exception(
                "Runtime product synchronization failed after Command settlement",
                extra={
                    "run_id": run.run_id,
                    "command_id": command.id,
                    "checkpoint_id": (
                        checkpoint.checkpoint_id if checkpoint is not None else None
                    ),
                },
            )

    async def _handle_pre_command(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        if self._pre_command_handler is None:
            return
        try:
            await self._pre_command_handler.handle(
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
        except RetryableCommandError:
            raise
        except Exception as exc:
            raise RetryableCommandError(
                "pre_command_handler_failed",
                "pre-command side effects did not complete",
            ) from exc

    async def _process_locked(
        self,
        connection: AsyncConnection,
        command: RuntimeCommandRecord,
        run: RuntimeRunRecord,
    ) -> CommandWorkResult:
        if command.command_type not in _COMMAND_TYPES:
            return await self._reject(command, "unsupported_command")

        await self._begin_attempt(command)

        command_checkpoint = await self._checkpoint_reader.read_for_command(
            connection=connection,
            run=run,
            command=command,
        )
        if command_checkpoint is not None:
            self._validate_checkpoint(run, command_checkpoint, command=command)
            if command.command_type == "cancel":
                raise RetryableCommandError(
                    "cancel_checkpoint_forbidden",
                    "cancel must preserve an existing checkpoint, not create its own",
                )
            disposition = classify_checkpoint(command_checkpoint)
            if disposition in {"waiting", "terminal"}:
                await self._mark_applied(command, command_checkpoint.checkpoint_id)
                await self._sync_products_best_effort(
                    run=run,
                    command=command,
                    checkpoint=command_checkpoint,
                )
                return CommandWorkResult(
                    status="reconciled",
                    command_id=command.id,
                    run_id=command.run_id,
                    checkpoint_id=command_checkpoint.checkpoint_id,
                )
            if disposition == "inconsistent":
                raise RetryableCommandError(
                    "inconsistent_checkpoint",
                    "checkpoint values, next, tasks, and interrupts disagree",
                )
            checkpoint = command_checkpoint
        else:
            checkpoint = await self._checkpoint_reader.read_latest(
                connection=connection,
                run=run,
            )
            if checkpoint is not None:
                self._validate_checkpoint(run, checkpoint)

            if command.command_type == "cancel":
                if checkpoint is None:
                    await self._reject_unstarted_start(command)
                    await self._mark_applied(command, None)
                    await self._sync_products_best_effort(
                        run=run,
                        command=command,
                        checkpoint=None,
                    )
                    return CommandWorkResult(
                        status="applied",
                        command_id=command.id,
                        run_id=command.run_id,
                    )
                disposition = classify_checkpoint(checkpoint)
                if disposition == "terminal":
                    return await self._reject(command, "already_terminal")
                if disposition == "inconsistent":
                    raise RetryableCommandError(
                        "inconsistent_checkpoint",
                        "cannot cancel from an internally inconsistent checkpoint",
                    )
                # The Thread lock proves no invocation is currently advancing.
                # Preserve the last committed checkpoint as the cancellation
                # boundary; the applied cancel Command is the control truth.
                await self._mark_applied(command, checkpoint.checkpoint_id)
                await self._sync_products_best_effort(
                    run=run,
                    command=command,
                    checkpoint=checkpoint,
                )
                return CommandWorkResult(
                    status="applied",
                    command_id=command.id,
                    run_id=command.run_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                )

            if command.command_type == "start" and checkpoint is not None:
                raise RetryableCommandError(
                    "start_checkpoint_conflict",
                    "start found a checkpoint for this Run without matching Command metadata",
                )
            if command.command_type == "resume" and checkpoint is None:
                return await self._reject(command, "thread_not_started")
            if checkpoint is not None and classify_checkpoint(checkpoint) == "terminal":
                return await self._reject(command, "already_terminal")

            await self._handle_pre_command(
                run=run,
                command=command,
                checkpoint=checkpoint,
            )

        try:
            await self._command_executor.execute(
                connection=connection,
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
        except CommandExecutionRejected as exc:
            return await self._reject(command, exc.code)

        observed = await self._checkpoint_reader.read_for_command(
            connection=connection,
            run=run,
            command=command,
        )
        if observed is None:
            raise CommandCheckpointNotObserved(command.id)
        self._validate_checkpoint(run, observed, command=command)
        disposition = classify_checkpoint(observed)
        if disposition == "inconsistent":
            raise RetryableCommandError(
                "inconsistent_checkpoint",
                "checkpoint values, next, tasks, and interrupts disagree",
            )
        if disposition not in {"waiting", "terminal"}:
            raise RetryableCommandError(
                "command_not_stable",
                "Command checkpoint remains runnable and must be continued",
            )
        await self._mark_applied(command, observed.checkpoint_id)
        await self._sync_products_best_effort(
            run=run,
            command=command,
            checkpoint=observed,
        )
        return CommandWorkResult(
            status="applied",
            command_id=command.id,
            run_id=command.run_id,
            checkpoint_id=observed.checkpoint_id,
        )

    async def run_once(self) -> CommandWorkResult:
        """Process at most one Command; callers own daemon polling/backoff."""
        command = await self._claim()
        if command is None:
            return CommandWorkResult(status="idle")
        if command.attempt_count >= self._max_attempts:
            return await self._reject(command, "reconciliation_required")

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(command, stop_heartbeat),
            name=f"runtime-command-heartbeat-{command.id}",
        )
        try:
            try:
                try:
                    run = await self._load_run(command)
                except CommandExecutionRejected as exc:
                    return await self._reject(command, exc.code)
                return await run_with_thread_lock(
                    self._lock_engine,
                    run.thread_id,
                    lambda connection: self._process_locked(connection, command, run),
                )
            except ThreadLockNotAcquired:
                await self._release_for_retry(command, "thread_lock_busy")
                return CommandWorkResult(
                    status="retry",
                    command_id=command.id,
                    run_id=command.run_id,
                    error_code="thread_lock_busy",
                )
            except RuntimeInvocationCancelled:
                # The Graph node deliberately raised before committing a
                # synthetic cancelled state. Settle this invocation, release
                # the real Thread lock, then let the durable cancel Command
                # apply against the preserved checkpoint.
                return await self._reject(command, "cancelled_before_apply")
            except ToolExecutionReconciliationPending as exc:
                if exc.defer_without_attempt:
                    await self._defer_without_attempt(command, exc.code)
                else:
                    await self._release_for_retry(command, exc.code)
                return CommandWorkResult(
                    status="retry",
                    command_id=command.id,
                    run_id=command.run_id,
                    error_code=exc.code,
                )
            except RetryableCommandError as exc:
                await self._release_for_retry(command, exc.code)
                return CommandWorkResult(
                    status="retry",
                    command_id=command.id,
                    run_id=command.run_id,
                    error_code=exc.code,
                )
            except Exception:
                await self._release_for_retry(command, "command_execution_failed")
                raise
        finally:
            stop_heartbeat.set()
            await heartbeat
