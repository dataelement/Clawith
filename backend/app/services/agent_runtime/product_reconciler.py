"""Retry product synchronization without re-entering the Agent Graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Literal
import uuid

from sqlalchemy import and_, or_, select

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_tool_execution import AgentToolExecution
from app.models.chat_session import ChatSession
from app.services.agent_runtime.command_worker import (
    RuntimePostCheckpointHandler,
    RuntimeSessionFactory,
    classify_checkpoint,
    runtime_command_record,
)
from app.services.agent_runtime.group_runtime_tools import (
    GROUP_WORKSPACE_MUTATION_TOOL_NAMES,
    GroupRuntimeToolService,
    GroupWorkspaceReconciliationPending,
)
from app.services.agent_runtime.langgraph_driver import LangGraphRuntimeDriver
from app.services.agent_runtime.persistence import mark_command_product_synced
from app.services.agent_runtime.run_state_reader import runtime_run_record
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    mark_tool_execution_failed,
    mark_tool_execution_succeeded,
    mark_tool_execution_unknown,
    takeover_tool_execution_for_reconciliation,
)


logger = logging.getLogger(__name__)
ReconcileStatus = Literal["idle", "synced", "retry", "quarantined"]


@dataclass(frozen=True, slots=True)
class GroupWorkspaceReconcileCandidate:
    """Existing ledger + Group scope needed for recovery outside the Graph."""

    execution: AgentToolExecution
    group_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ProductReconcileResult:
    status: ReconcileStatus
    command_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    tool_execution_id: uuid.UUID | None = None
    error_code: str | None = None


class RuntimeProductReconciler:
    """Replay idempotent products for applied Commands marked incomplete."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        checkpoint_reader: LangGraphRuntimeDriver,
        handler: RuntimePostCheckpointHandler,
        group_tool_service: GroupRuntimeToolService | None = None,
        lease_ttl_seconds: int = 300,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._session_factory = session_factory
        self._checkpoint_reader = checkpoint_reader
        self._handler = handler
        self._group_tool_service = group_tool_service or GroupRuntimeToolService(
            session_factory=session_factory
        )
        self._lease_ttl_seconds = lease_ttl_seconds

    async def _next(self) -> tuple[AgentRun, AgentRunCommand] | None:
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentRunCommand)
                    .where(
                        AgentRunCommand.status == "applied",
                        AgentRunCommand.error_code == "product_sync_pending",
                    )
                    .order_by(
                        AgentRunCommand.applied_at,
                        AgentRunCommand.created_at,
                        AgentRunCommand.id,
                    )
                    .limit(1)
                )
                command = result.scalar_one_or_none()
                if command is None:
                    return None
                run_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == command.tenant_id,
                        AgentRun.id == command.run_id,
                    )
                )
                run = run_result.scalar_one_or_none()
                if run is None:
                    return None
                return run, command

    async def _mark_synced(self, command: AgentRunCommand) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                await mark_command_product_synced(
                    db,
                    tenant_id=command.tenant_id,
                    command_id=command.id,
                )

    async def _next_group_workspace(
        self,
    ) -> GroupWorkspaceReconcileCandidate | None:
        now = datetime.now(UTC)
        unknown_recheck_before = now - timedelta(
            seconds=self._lease_ttl_seconds
        )
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentToolExecution, ChatSession.group_id)
                    .join(
                        AgentRun,
                        (AgentRun.tenant_id == AgentToolExecution.tenant_id)
                        & (AgentRun.id == AgentToolExecution.run_id),
                    )
                    .outerjoin(
                        ChatSession,
                        (ChatSession.tenant_id == AgentRun.tenant_id)
                        & (ChatSession.id == AgentRun.session_id),
                    )
                    .where(
                        AgentToolExecution.tool_name.in_(
                            GROUP_WORKSPACE_MUTATION_TOOL_NAMES
                        ),
                        or_(
                            and_(
                                AgentToolExecution.status == "started",
                                or_(
                                    AgentToolExecution.lease_expires_at.is_(None),
                                    AgentToolExecution.lease_expires_at <= now,
                                ),
                            ),
                            and_(
                                AgentToolExecution.status == "unknown",
                                or_(
                                    AgentToolExecution.completed_at.is_(None),
                                    AgentToolExecution.completed_at
                                    <= unknown_recheck_before,
                                ),
                            ),
                        ),
                    )
                    .order_by(
                        AgentToolExecution.lease_expires_at.asc().nulls_first(),
                        AgentToolExecution.started_at,
                        AgentToolExecution.id,
                    )
                    .limit(1)
                )
                row = result.first()
                if row is None:
                    return None
                return GroupWorkspaceReconcileCandidate(
                    execution=row[0],
                    group_id=row[1],
                )

    async def _takeover_group_workspace(
        self,
        candidate: GroupWorkspaceReconcileCandidate,
        *,
        lease_owner: str,
    ):
        async with self._session_factory() as db:
            async with db.begin():
                return await takeover_tool_execution_for_reconciliation(
                    db,
                    tenant_id=candidate.execution.tenant_id,
                    execution_id=candidate.execution.id,
                    lease_owner=lease_owner,
                    lease_ttl_seconds=self._lease_ttl_seconds,
                    reopen_unknown=candidate.execution.status == "unknown",
                )

    async def _settle_group_workspace(
        self,
        candidate: GroupWorkspaceReconcileCandidate,
        *,
        lease_owner: str,
        outcome: ToolExecutionOutcome,
    ) -> None:
        settle = {
            "succeeded": mark_tool_execution_succeeded,
            "failed": mark_tool_execution_failed,
            "unknown": mark_tool_execution_unknown,
        }[outcome.status]
        async with self._session_factory() as db:
            async with db.begin():
                await settle(
                    db,
                    tenant_id=candidate.execution.tenant_id,
                    execution_id=candidate.execution.id,
                    lease_owner=lease_owner,
                    result_summary=outcome.result_summary,
                    result_ref=outcome.result_ref,
                    error_code=outcome.error_code,
                    retryable=outcome.retryable,
                    artifact_refs=outcome.artifact_refs,
                    evidence_refs=outcome.evidence_refs,
                    metadata=outcome.metadata,
                )

    @staticmethod
    def _group_result(
        candidate: GroupWorkspaceReconcileCandidate,
        outcome: ToolExecutionOutcome,
    ) -> ProductReconcileResult:
        return ProductReconcileResult(
            status=("synced" if outcome.status == "succeeded" else "quarantined"),
            run_id=candidate.execution.run_id,
            tool_execution_id=candidate.execution.id,
            error_code=(
                outcome.error_code
                if outcome.status != "succeeded"
                else None
            ),
        )

    async def _run_group_workspace_once(
        self,
        candidate: GroupWorkspaceReconcileCandidate,
    ) -> ProductReconcileResult:
        lease_owner = f"product-reconcile:{uuid.uuid4()}"
        try:
            takeover = await self._takeover_group_workspace(
                candidate,
                lease_owner=lease_owner,
            )
        except Exception:
            logger.exception(
                "Group workspace fence takeover failed",
                extra={"tool_execution_id": candidate.execution.id},
            )
            return ProductReconcileResult(
                status="retry",
                run_id=candidate.execution.run_id,
                tool_execution_id=candidate.execution.id,
                error_code="group_workspace_takeover_failed",
            )
        if takeover.active:
            return ProductReconcileResult(
                status="retry",
                run_id=candidate.execution.run_id,
                tool_execution_id=candidate.execution.id,
                error_code="group_workspace_active_lease",
            )
        if takeover.terminal_outcome is not None:
            return self._group_result(candidate, takeover.terminal_outcome)
        if not takeover.acquired:
            return ProductReconcileResult(
                status="retry",
                run_id=candidate.execution.run_id,
                tool_execution_id=candidate.execution.id,
                error_code="group_workspace_fence_unavailable",
            )

        try:
            if candidate.group_id is None:
                outcome = ToolExecutionOutcome(
                    status="unknown",
                    result_summary=(
                        "Group workspace scope is unavailable; durable storage "
                        "facts cannot be reconciled automatically."
                    ),
                    result_ref=None,
                    error_code="group_workspace_scope_unavailable",
                    retryable=False,
                    metadata={
                        "operation_id": str(candidate.execution.id),
                        "operation": (
                            "write"
                            if candidate.execution.tool_name
                            == "group_write_workspace_file"
                            else "delete"
                        ),
                    },
                )
            else:
                outcome = (
                    await self._group_tool_service.reconcile_workspace_operation_by_scope(
                        tenant_id=candidate.execution.tenant_id,
                        group_id=candidate.group_id,
                        tool_name=candidate.execution.tool_name,
                        operation_id=candidate.execution.id,
                        lease_owner=lease_owner,
                    )
                )
            await self._settle_group_workspace(
                candidate,
                lease_owner=lease_owner,
                outcome=outcome,
            )
        except GroupWorkspaceReconciliationPending as exc:
            return ProductReconcileResult(
                status="retry",
                run_id=candidate.execution.run_id,
                tool_execution_id=candidate.execution.id,
                error_code=exc.code,
            )
        except Exception:
            logger.exception(
                "Group workspace reconciliation failed",
                extra={"tool_execution_id": candidate.execution.id},
            )
            return ProductReconcileResult(
                status="retry",
                run_id=candidate.execution.run_id,
                tool_execution_id=candidate.execution.id,
                error_code="group_workspace_reconciliation_failed",
            )
        return self._group_result(candidate, outcome)

    async def run_once(self) -> ProductReconcileResult:
        group_candidate = await self._next_group_workspace()
        if group_candidate is not None:
            return await self._run_group_workspace_once(group_candidate)
        candidate = await self._next()
        if candidate is None:
            return ProductReconcileResult(status="idle")
        run, command = candidate
        run_record = runtime_run_record(run)
        command_record = runtime_command_record(command)
        checkpoint = None
        if command.applied_checkpoint_id is not None:
            checkpoint = await self._checkpoint_reader.read_checkpoint(
                run=run_record,
                checkpoint_id=command.applied_checkpoint_id,
            )
            if checkpoint is None:
                return ProductReconcileResult(
                    status="retry",
                    command_id=command.id,
                    run_id=run.id,
                    error_code="checkpoint_not_found",
                )
            if checkpoint.metadata.get("clawith_run_id") != str(run.id):
                return ProductReconcileResult(
                    status="retry",
                    command_id=command.id,
                    run_id=run.id,
                    error_code="checkpoint_identity_mismatch",
                )
            if (
                command.command_type != "cancel"
                and checkpoint.metadata.get("clawith_command_id") != str(command.id)
            ):
                return ProductReconcileResult(
                    status="retry",
                    command_id=command.id,
                    run_id=run.id,
                    error_code="checkpoint_command_mismatch",
                )
            if command.command_type != "cancel" and classify_checkpoint(checkpoint) not in {
                "waiting",
                "terminal",
            }:
                return ProductReconcileResult(
                    status="retry",
                    command_id=command.id,
                    run_id=run.id,
                    error_code="checkpoint_not_stable",
                )
        elif command.command_type != "cancel":
            return ProductReconcileResult(
                status="retry",
                command_id=command.id,
                run_id=run.id,
                error_code="checkpoint_not_found",
            )

        try:
            await self._handler.handle(
                run=run_record,
                command=command_record,
                checkpoint=checkpoint,
            )
            await self._mark_synced(command)
        except Exception:
            logger.exception(
                "Runtime product reconciliation failed",
                extra={"command_id": command.id, "run_id": run.id},
            )
            return ProductReconcileResult(
                status="retry",
                command_id=command.id,
                run_id=run.id,
                error_code="product_sync_failed",
            )
        return ProductReconcileResult(
            status="synced",
            command_id=command.id,
            run_id=run.id,
        )


__all__ = [
    "GroupWorkspaceReconcileCandidate",
    "ProductReconcileResult",
    "RuntimeProductReconciler",
]
