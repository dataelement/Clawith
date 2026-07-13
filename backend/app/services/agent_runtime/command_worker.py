"""Reliable Command Inbox orchestration around one authoritative checkpoint."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
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
    claim_next_command,
    mark_command_applied,
    mark_command_rejected,
    release_command_claim,
    renew_command_claim,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RunRegistrySnapshot,
    RuntimeGraphState,
)
from app.services.agent_runtime.thread_lock import ThreadLockNotAcquired, run_with_thread_lock


logger = logging.getLogger(__name__)

RuntimeCommandType = Literal["start", "resume", "cancel"]
CommandWorkStatus = Literal["idle", "applied", "reconciled", "rejected", "retry"]
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
    registry: RunRegistrySnapshot


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


@dataclass(frozen=True, slots=True)
class CheckpointObservation:
    """Latest checkpoint state observed through the installed Checkpointer API."""

    checkpoint_id: str
    state: RuntimeGraphState


class RuntimeCheckpointReader(Protocol):
    """Read the latest committed checkpoint for a validated Run thread."""

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
    """Apply idempotent side effects after a command is visible in a checkpoint."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation,
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


def _command_record(command: AgentRunCommand) -> RuntimeCommandRecord:
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
                return _command_record(command)

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
                if run.runtime_thread_id != str(run.id):
                    raise RetryableCommandError(
                        "runtime_identity_mismatch",
                        "Run thread_id must equal run_id",
                    )
                if run.model_id is None or not run.graph_name or not run.graph_version:
                    raise RetryableCommandError(
                        "invalid_graph_identity",
                        "LangGraph Run is missing pinned model or graph identity",
                    )
                registry = RunRegistrySnapshot(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
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
                )
                return RuntimeRunRecord(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    thread_id=run.runtime_thread_id,
                    runtime_type=run.runtime_type,
                    registry=registry,
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
        checkpoint_id: str,
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

    @staticmethod
    def _validate_checkpoint(
        run: RuntimeRunRecord,
        observation: CheckpointObservation,
    ) -> None:
        if not observation.checkpoint_id.strip():
            raise RetryableCommandError(
                "invalid_checkpoint_id",
                "checkpoint reader returned a blank checkpoint ID",
            )
        try:
            registry = observation.state["registry"]
            lifecycle = observation.state["lifecycle"]
            status = lifecycle["status"]
        except (KeyError, TypeError) as exc:
            raise RetryableCommandError(
                "invalid_checkpoint_state",
                "checkpoint is missing Runtime identity or lifecycle state",
            ) from exc
        if registry != run.registry:
            raise RetryableCommandError(
                "checkpoint_identity_mismatch",
                "checkpoint tenant, Run, model, or graph identity differs from the Run Registry",
            )
        if status not in _LIFECYCLE_STATUSES:
            raise RetryableCommandError(
                "invalid_checkpoint_status",
                "checkpoint lifecycle status is unsupported",
            )
        applied_ids = lifecycle.get("last_applied_command_ids", [])
        if not isinstance(applied_ids, list) or any(not isinstance(value, str) for value in applied_ids):
            raise RetryableCommandError(
                "invalid_checkpoint_command_ids",
                "checkpoint command reconciliation IDs are malformed",
            )

    @classmethod
    def _checkpoint_contains(
        cls,
        run: RuntimeRunRecord,
        observation: CheckpointObservation,
        command_id: uuid.UUID,
    ) -> bool:
        cls._validate_checkpoint(run, observation)
        return str(command_id) in observation.state["lifecycle"].get(
            "last_applied_command_ids",
            [],
        )

    @classmethod
    def _command_is_observed(
        cls,
        run: RuntimeRunRecord,
        observation: CheckpointObservation,
        command: RuntimeCommandRecord,
    ) -> bool:
        if not cls._checkpoint_contains(run, observation, command.id):
            return False
        if command.command_type == "cancel" and observation.state["lifecycle"]["status"] != "cancelled":
            raise RetryableCommandError(
                "cancel_not_observed",
                "cancel command ID is present but checkpoint lifecycle is not cancelled",
            )
        return True

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

    async def _handle_observed_checkpoint(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        try:
            await self._post_checkpoint_handler.handle(
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
        except RetryableCommandError:
            raise
        except Exception as exc:
            raise RetryableCommandError(
                "post_checkpoint_handler_failed",
                "post-checkpoint side effects did not complete",
            ) from exc

    async def _process_locked(
        self,
        connection: AsyncConnection,
        command: RuntimeCommandRecord,
    ) -> CommandWorkResult:
        try:
            run = await self._load_run(command)
        except CommandExecutionRejected as exc:
            return await self._reject(command, exc.code)

        checkpoint = await self._checkpoint_reader.read_latest(
            connection=connection,
            run=run,
        )
        if checkpoint is not None and self._command_is_observed(run, checkpoint, command):
            await self._handle_observed_checkpoint(
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
            await self._mark_applied(command, checkpoint.checkpoint_id)
            return CommandWorkResult(
                status="reconciled",
                command_id=command.id,
                run_id=command.run_id,
                checkpoint_id=checkpoint.checkpoint_id,
            )

        if command.command_type not in _COMMAND_TYPES:
            return await self._reject(command, "unsupported_command")
        if command.command_type == "start" and checkpoint is not None:
            raise RetryableCommandError(
                "start_checkpoint_conflict",
                "start command found an existing checkpoint without its command ID",
            )
        if command.command_type != "start" and checkpoint is None:
            return await self._reject(command, "thread_not_started")
        if checkpoint is not None:
            status = checkpoint.state["lifecycle"]["status"]
            if status in _TERMINAL_STATUSES:
                terminal_code = "terminal_cancel" if command.command_type == "cancel" else "terminal_resume"
                return await self._reject(command, terminal_code)

        try:
            await self._command_executor.execute(
                connection=connection,
                run=run,
                command=command,
                checkpoint=checkpoint,
            )
        except CommandExecutionRejected as exc:
            return await self._reject(command, exc.code)

        observed = await self._checkpoint_reader.read_latest(
            connection=connection,
            run=run,
        )
        if observed is None or not self._command_is_observed(run, observed, command):
            raise CommandCheckpointNotObserved(command.id)
        await self._handle_observed_checkpoint(
            run=run,
            command=command,
            checkpoint=observed,
        )
        await self._mark_applied(command, observed.checkpoint_id)
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

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(command, stop_heartbeat),
            name=f"runtime-command-heartbeat-{command.id}",
        )
        try:
            try:
                return await run_with_thread_lock(
                    self._lock_engine,
                    command.run_id,
                    lambda connection: self._process_locked(connection, command),
                )
            except ThreadLockNotAcquired:
                await self._release_for_retry(command, "thread_lock_busy")
                return CommandWorkResult(
                    status="retry",
                    command_id=command.id,
                    run_id=command.run_id,
                    error_code="thread_lock_busy",
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
