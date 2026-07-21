"""Product reconciliation never re-enters the settled Agent Graph."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime.command_worker import CheckpointObservation
from app.services.agent_runtime.product_reconciler import (
    GroupWorkspaceReconcileCandidate,
    RuntimeProductReconciler,
)
from app.services.agent_runtime.state import RunInputSnapshots
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    ToolExecutionTakeover,
)


def _run() -> AgentRun:
    run_id = uuid.uuid4()
    return AgentRun(
        id=run_id,
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id="shared-thread",
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )


def _command(run: AgentRun) -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="start",
        payload={"message": "hello"},
        idempotency_key="start:1",
        status="applied",
        attempt_count=1,
        applied_checkpoint_id="checkpoint-stable",
        error_code="product_sync_pending",
    )


def _checkpoint(run: AgentRun, command: AgentRunCommand) -> CheckpointObservation:
    return CheckpointObservation(
        checkpoint_id="checkpoint-stable",
        state={
            "snapshots": RunInputSnapshots(
                session_context={},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": "completed",
                "next_route": "terminal",
                "final_answer": "done",
            },
        },
        metadata={
            "clawith_run_id": str(run.id),
            "clawith_command_id": str(command.id),
        },
    )


class _Driver:
    def __init__(self, checkpoint: CheckpointObservation) -> None:
        self.checkpoint = checkpoint
        self.reads: list[tuple[uuid.UUID, str]] = []
        self.execute = AsyncMock(side_effect=AssertionError("Graph must not replay"))

    async def read_checkpoint(self, *, run, checkpoint_id):
        self.reads.append((run.run_id, checkpoint_id))
        return self.checkpoint


class _Handler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def handle(self, *, run, command, checkpoint) -> None:
        del run, command, checkpoint
        self.calls += 1
        if self.error is not None:
            raise self.error


class _GroupToolReconciler:
    def __init__(self, outcome: ToolExecutionOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    async def reconcile_workspace_operation_by_scope(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome


def _group_workspace_candidate(run: AgentRun) -> GroupWorkspaceReconcileCandidate:
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        tool_call_id="group-write-call",
        tool_name="group_write_workspace_file",
        assistant_message_id="assistant-message",
        arguments_hash="hash",
        sanitized_arguments={"path": "report.md", "content": "final"},
        effect="write",
        retry_policy="conditional",
        result_metadata={},
        status="started",
        lease_owner="exhausted-command-invocation",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    return GroupWorkspaceReconcileCandidate(
        execution=execution,
        group_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_applied_pending_product_sync_replays_only_idempotent_handler() -> None:
    run = _run()
    command = _command(run)
    checkpoint = _checkpoint(run, command)
    driver = _Driver(checkpoint)
    handler = _Handler()
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=driver,  # type: ignore[arg-type]
        handler=handler,
    )
    reconciler._next = AsyncMock(return_value=(run, command))  # type: ignore[method-assign]
    reconciler._next_group_workspace = AsyncMock(return_value=None)  # type: ignore[method-assign]
    reconciler._mark_synced = AsyncMock()  # type: ignore[method-assign]

    result = await reconciler.run_once()

    assert result.status == "synced"
    assert driver.reads == [(run.id, "checkpoint-stable")]
    assert handler.calls == 1
    driver.execute.assert_not_awaited()
    reconciler._mark_synced.assert_awaited_once_with(command)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_failed_product_retry_keeps_receipt_and_never_replays_graph() -> None:
    run = _run()
    command = _command(run)
    driver = _Driver(_checkpoint(run, command))
    handler = _Handler(RuntimeError("delivery unavailable"))
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=driver,  # type: ignore[arg-type]
        handler=handler,
    )
    reconciler._next = AsyncMock(return_value=(run, command))  # type: ignore[method-assign]
    reconciler._next_group_workspace = AsyncMock(return_value=None)  # type: ignore[method-assign]
    reconciler._mark_synced = AsyncMock()  # type: ignore[method-assign]

    result = await reconciler.run_once()

    assert result.status == "retry"
    assert result.error_code == "product_sync_failed"
    driver.execute.assert_not_awaited()
    reconciler._mark_synced.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_exhausted_command_group_revision_is_reconciled_without_graph_reentry() -> None:
    run = _run()
    candidate = _group_workspace_candidate(run)
    outcome = ToolExecutionOutcome(
        status="succeeded",
        result_summary="Group workspace revision finalized",
        result_ref=None,
        metadata={"operation_id": str(candidate.execution.id)},
    )
    group_reconciler = _GroupToolReconciler(outcome)
    driver = _Driver(_checkpoint(run, _command(run)))
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=driver,  # type: ignore[arg-type]
        handler=_Handler(),
        group_tool_service=group_reconciler,  # type: ignore[arg-type]
    )
    reconciler._next = AsyncMock(return_value=None)  # type: ignore[method-assign]
    reconciler._next_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=candidate
    )
    reconciler._settle_group_workspace = AsyncMock()  # type: ignore[method-assign]

    takeover = ToolExecutionTakeover(
            execution=candidate.execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
    )
    reconciler._takeover_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=takeover
    )

    result = await reconciler.run_once()

    assert result.status == "synced"
    assert result.tool_execution_id == candidate.execution.id
    assert group_reconciler.calls[0]["operation_id"] == candidate.execution.id
    assert group_reconciler.calls[0]["lease_owner"].startswith("product-reconcile:")
    assert group_reconciler.calls[0]["lease_owner"] != candidate.execution.lease_owner
    reconciler._settle_group_workspace.assert_awaited_once()  # type: ignore[attr-defined]
    driver.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fenced_missing_prepare_becomes_durable_known_failure() -> None:
    run = _run()
    candidate = _group_workspace_candidate(run)
    outcome = ToolExecutionOutcome(
        status="failed",
        result_summary="No prepared Group workspace operation exists",
        result_ref=None,
        error_code="group_workspace_operation_not_prepared",
        retryable=False,
        metadata={"operation_id": str(candidate.execution.id)},
    )
    group_reconciler = _GroupToolReconciler(outcome)
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=_Driver(_checkpoint(run, _command(run))),  # type: ignore[arg-type]
        handler=_Handler(),
        group_tool_service=group_reconciler,  # type: ignore[arg-type]
    )
    reconciler._next = AsyncMock(return_value=None)  # type: ignore[method-assign]
    reconciler._next_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=candidate
    )
    reconciler._settle_group_workspace = AsyncMock()  # type: ignore[method-assign]

    takeover = ToolExecutionTakeover(
            execution=candidate.execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
    )
    reconciler._takeover_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=takeover
    )

    result = await reconciler.run_once()

    assert result.status == "quarantined"
    assert result.error_code == "group_workspace_operation_not_prepared"
    settled_outcome = reconciler._settle_group_workspace.await_args.kwargs[  # type: ignore[attr-defined]
        "outcome"
    ]
    assert settled_outcome.status == "failed"


@pytest.mark.asyncio
async def test_product_reconciler_defers_active_group_lease_without_read_or_settle() -> None:
    run = _run()
    candidate = _group_workspace_candidate(run)
    group_reconciler = _GroupToolReconciler(
        ToolExecutionOutcome(status="succeeded", result_summary="done", result_ref=None)
    )
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=_Driver(_checkpoint(run, _command(run))),  # type: ignore[arg-type]
        handler=_Handler(),
        group_tool_service=group_reconciler,  # type: ignore[arg-type]
    )
    reconciler._next = AsyncMock(return_value=None)  # type: ignore[method-assign]
    reconciler._next_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=candidate
    )
    reconciler._settle_group_workspace = AsyncMock()  # type: ignore[method-assign]

    active = ToolExecutionTakeover(
            execution=candidate.execution,
            acquired=False,
            active=True,
            terminal_outcome=None,
    )
    reconciler._takeover_group_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=active
    )

    result = await reconciler.run_once()

    assert result.status == "retry"
    assert result.error_code == "group_workspace_active_lease"
    assert group_reconciler.calls == []
    reconciler._settle_group_workspace.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_late_storage_success_reopens_unknown_and_forward_finalizes() -> None:
    run = _run()
    candidate = _group_workspace_candidate(run)
    outcomes = [
        ToolExecutionOutcome(
            status="unknown",
            result_summary="Prepared revision did not match storage yet",
            result_ref=None,
            error_code="group_workspace_reconciliation_conflict",
            metadata={"operation_id": str(candidate.execution.id)},
        ),
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Late storage success is now proven by revision/hash",
            result_ref=None,
            metadata={"operation_id": str(candidate.execution.id)},
        ),
    ]

    class _SequentialReconciler:
        def __init__(self) -> None:
            self.calls = 0

        async def reconcile_workspace_operation_by_scope(self, **_kwargs):
            outcome = outcomes[self.calls]
            self.calls += 1
            return outcome

    group_reconciler = _SequentialReconciler()
    reconciler = RuntimeProductReconciler(
        session_factory=AsyncMock(),  # type: ignore[arg-type]
        checkpoint_reader=_Driver(_checkpoint(run, _command(run))),  # type: ignore[arg-type]
        handler=_Handler(),
        group_tool_service=group_reconciler,  # type: ignore[arg-type]
    )

    async def takeover(_candidate, *, lease_owner):
        candidate.execution.status = "started"
        candidate.execution.lease_owner = lease_owner
        return ToolExecutionTakeover(
            execution=candidate.execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
        )

    async def settle(_candidate, *, lease_owner, outcome):
        assert lease_owner == candidate.execution.lease_owner
        candidate.execution.status = outcome.status
        candidate.execution.result_summary = outcome.result_summary
        candidate.execution.result_metadata = dict(outcome.metadata)
        candidate.execution.completed_at = datetime.now(UTC)

    reconciler._takeover_group_workspace = takeover  # type: ignore[method-assign]
    reconciler._settle_group_workspace = settle  # type: ignore[method-assign]

    first = await reconciler._run_group_workspace_once(candidate)
    assert first.status == "quarantined"
    assert candidate.execution.status == "unknown"

    # The original storage dispatch finishes after the first read-only probe.
    # A later probe reopens only the unknown ledger fact, reads durable storage,
    # and forward-finalizes; no mutation entrypoint is available here.
    second = await reconciler._run_group_workspace_once(candidate)

    assert second.status == "synced"
    assert candidate.execution.status == "succeeded"
    assert group_reconciler.calls == 2


@pytest.mark.asyncio
async def test_background_scan_reclaims_expired_started_and_delayed_unknown_rows() -> None:
    statements = []

    class _Result:
        def first(self):
            return None

    class _Begin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _DB:
        def begin(self):
            return _Begin()

        async def execute(self, statement):
            statements.append(statement)
            return _Result()

    @asynccontextmanager
    async def factory():
        yield _DB()

    run = _run()
    reconciler = RuntimeProductReconciler(
        session_factory=factory,  # type: ignore[arg-type]
        checkpoint_reader=_Driver(_checkpoint(run, _command(run))),  # type: ignore[arg-type]
        handler=_Handler(),
    )

    assert await reconciler._next_group_workspace() is None
    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN chat_sessions" in sql
    assert "agent_tool_executions.status = 'started'" in sql
    assert "agent_tool_executions.lease_expires_at" in sql
    assert "agent_tool_executions.status = 'unknown'" in sql
    assert "agent_tool_executions.completed_at" in sql
