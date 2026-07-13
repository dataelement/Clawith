"""Focused tests for the Runtime Tool Execution Ledger service."""

from collections import deque
from datetime import UTC, datetime
import inspect
import math
import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime import tool_execution


_NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
_ARGUMENTS = {"channel": "ops", "message": "hello"}
_SANITIZED_ARGUMENTS = {"channel": "ops", "message": "[redacted]"}


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _NestedTransaction:
    def __init__(self, db: "_FakeSession"):
        self.db = db

    async def __aenter__(self):
        self.db.nested_entries += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.nested_exit_exceptions.append(exc_type)
        return False


class _FakeSession:
    def __init__(self, *results, flush_errors=()):
        self.results = deque(results)
        self.flush_errors = deque(flush_errors)
        self.statements = []
        self.added = []
        self.flush_count = 0
        self.nested_entries = 0
        self.nested_exit_exceptions = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database execute")
        return _ScalarResult(self.results.popleft())

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        if self.flush_errors:
            error = self.flush_errors.popleft()
            if error is not None:
                raise error

    def begin_nested(self):
        return _NestedTransaction(self)

    async def commit(self):
        raise AssertionError("ledger helpers must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("ledger helpers must not roll back the caller transaction")


def _persisted_arguments(
    *,
    effect: str = "external_write",
    retry_policy: str = "never",
):
    return tool_execution._stored_arguments(
        _SANITIZED_ARGUMENTS,
        side_effect_classification=effect,
        retry_policy=retry_policy,
    )


def _execution(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    tool_call_id: str = "call-1",
    effect: str = "external_write",
    retry_policy: str = "never",
    lease_owner: str = "worker-1",
    result_summary: str | None = None,
    result_ref: str | None = None,
) -> AgentToolExecution:
    return AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        tool_name="send_message",
        assistant_message_id="assistant-message-1",
        arguments_hash=tool_execution.fingerprint_arguments(_ARGUMENTS),
        sanitized_arguments=_persisted_arguments(
            effect=effect,
            retry_policy=retry_policy,
        ),
        request_ref="request://1",
        status=status,
        result_summary=result_summary,
        result_ref=result_ref,
        lease_owner=lease_owner,
        started_at=_NOW,
    )


async def _reserve(
    db,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    effect: str = "external_write",
    retry_policy: str = "never",
    retry_failed: bool = False,
    arguments: dict | None = None,
):
    return await tool_execution.reserve_tool_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id="call-1",
        tool_name="send_message",
        assistant_message_id="assistant-message-1",
        arguments=arguments or _ARGUMENTS,
        sanitized_arguments=_SANITIZED_ARGUMENTS,
        request_ref="request://1",
        side_effect_classification=effect,
        retry_policy=retry_policy,
        lease_owner="worker-1",
        lease_ttl_seconds=60,
        retry_failed=retry_failed,
        clock=lambda: _NOW,
    )


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_argument_fingerprint_is_canonical_and_rejects_non_json_values():
    first = tool_execution.fingerprint_arguments({"message": "你好", "nested": {"b": 2, "a": 1}})
    second = tool_execution.fingerprint_arguments({"nested": {"a": 1, "b": 2}, "message": "你好"})

    assert first == second
    assert len(first) == 64

    with pytest.raises(tool_execution.ToolExecutionError) as exc_info:
        tool_execution.fingerprint_arguments({"not_finite": math.inf})
    assert exc_info.value.code == "invalid_tool_execution_input"


@pytest.mark.asyncio
async def test_inspection_exposes_not_started_without_persisting_a_fake_status():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = _FakeSession(run_id, None)

    result = await tool_execution.inspect_tool_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id="call-1",
    )

    assert result == tool_execution.ToolExecutionInspection(
        status="not_started",
        execution=None,
    )
    assert db.added == []
    assert db.flush_count == 0
    ledger_sql = _sql(db.statements[1])
    assert "agent_tool_executions.tenant_id" in ledger_sql
    assert "agent_tool_executions.run_id" in ledger_sql
    assert "agent_tool_executions.tool_call_id" in ledger_sql
    assert "FOR UPDATE" not in ledger_sql


@pytest.mark.asyncio
async def test_new_reservation_atomically_persists_started_and_execution_metadata():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = _FakeSession(run_id, None)

    reservation = await _reserve(db, tenant_id=tenant_id, run_id=run_id)

    assert reservation.created is True
    assert reservation.can_execute is True
    assert reservation.blocked is False
    assert reservation.status == "started"
    assert db.added == [reservation.execution]
    assert db.flush_count == 1
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [None]
    assert reservation.execution.arguments_hash == tool_execution.fingerprint_arguments(_ARGUMENTS)
    assert reservation.execution.sanitized_arguments == {
        "arguments": _SANITIZED_ARGUMENTS,
        "__clawith_tool_execution__": {
            "version": 1,
            "side_effect_classification": "external_write",
            "retry_policy": "never",
        },
    }
    assert reservation.execution.request_ref == "request://1"
    assert reservation.execution.lease_owner == "worker-1"
    assert reservation.execution.lease_expires_at == datetime(2026, 7, 13, 13, 1, tzinfo=UTC)

    locked_sql = _sql(db.statements[1])
    assert "FOR UPDATE" in locked_sql


@pytest.mark.asyncio
async def test_succeeded_reservation_reuses_receipt_and_never_executes_again():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    existing = _execution(
        tenant_id=tenant_id,
        run_id=run_id,
        status="succeeded",
        result_summary="message sent",
        result_ref="message://42",
    )
    db = _FakeSession(run_id, existing)

    reservation = await _reserve(db, tenant_id=tenant_id, run_id=run_id)

    assert reservation.created is False
    assert reservation.can_execute is False
    assert reservation.blocked is False
    assert reservation.reusable_result == tool_execution.ToolExecutionOutcome(
        status="succeeded",
        result_summary="message sent",
        result_ref="message://42",
    )
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "requires_confirmation", "error_code"),
    [
        ("started", False, "tool_execution_started"),
        ("unknown", True, "tool_outcome_unknown"),
    ],
)
async def test_started_and_unknown_always_fail_closed_for_reconciliation(
    status,
    requires_confirmation,
    error_code,
):
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    existing = _execution(tenant_id=tenant_id, run_id=run_id, status=status)
    # An expired/missing lease is not proof that an external write never happened.
    existing.lease_expires_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    db = _FakeSession(run_id, existing)

    reservation = await _reserve(db, tenant_id=tenant_id, run_id=run_id)

    assert reservation.can_execute is False
    assert reservation.blocked is True
    assert reservation.reconciliation_required is True
    assert reservation.requires_confirmation is requires_confirmation
    assert reservation.error_code == error_code
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_idempotency_key_rejects_changed_arguments_or_execution_metadata():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    existing = _execution(tenant_id=tenant_id, run_id=run_id, status="started")

    changed_arguments_db = _FakeSession(run_id, existing)
    with pytest.raises(tool_execution.ToolExecutionError) as arguments_error:
        await _reserve(
            changed_arguments_db,
            tenant_id=tenant_id,
            run_id=run_id,
            arguments={"channel": "finance", "message": "hello"},
        )
    assert arguments_error.value.code == "tool_call_idempotency_mismatch"
    assert "arguments_hash" in str(arguments_error.value)

    changed_effect_db = _FakeSession(run_id, existing)
    with pytest.raises(tool_execution.ToolExecutionError) as effect_error:
        await _reserve(
            changed_effect_db,
            tenant_id=tenant_id,
            run_id=run_id,
            effect="read",
            retry_policy="safe",
        )
    assert effect_error.value.code == "tool_call_idempotency_mismatch"
    assert "sanitized_arguments" in str(effect_error.value)


@pytest.mark.asyncio
async def test_failed_execution_only_retries_when_explicitly_safe_and_read_only():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    failed = _execution(
        tenant_id=tenant_id,
        run_id=run_id,
        status="failed",
        effect="read",
        retry_policy="safe",
        result_summary="temporary read failure",
    )

    blocked_db = _FakeSession(run_id, failed)
    blocked = await _reserve(
        blocked_db,
        tenant_id=tenant_id,
        run_id=run_id,
        effect="read",
        retry_policy="safe",
    )
    assert blocked.blocked is True
    assert blocked.prior_failure.result_summary == "temporary read failure"
    assert blocked.error_code == "tool_execution_failed"
    assert blocked_db.flush_count == 0

    retry_db = _FakeSession(run_id, failed)
    retry = await _reserve(
        retry_db,
        tenant_id=tenant_id,
        run_id=run_id,
        effect="read",
        retry_policy="safe",
        retry_failed=True,
    )
    assert retry.can_execute is True
    assert retry.retrying is True
    assert retry.prior_failure.result_summary == "temporary read failure"
    assert failed.status == "started"
    assert failed.result_summary is None
    assert failed.completed_at is None
    assert retry_db.flush_count == 1


@pytest.mark.asyncio
async def test_failed_external_write_cannot_be_retried_even_when_requested():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    failed = _execution(
        tenant_id=tenant_id,
        run_id=run_id,
        status="failed",
        effect="external_write",
        retry_policy="never",
        result_summary="known failure",
    )
    db = _FakeSession(run_id, failed)

    with pytest.raises(tool_execution.ToolExecutionError) as exc_info:
        await _reserve(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            retry_failed=True,
        )

    assert exc_info.value.code == "unsafe_tool_retry"
    assert failed.status == "failed"
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_concurrent_insert_uses_savepoint_and_loser_must_not_execute():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    winner = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    conflict = IntegrityError(
        statement="INSERT INTO agent_tool_executions",
        params={},
        orig=Exception("uq_agent_tool_executions_run_tool_call"),
    )
    db = _FakeSession(run_id, None, winner, flush_errors=(conflict,))

    reservation = await _reserve(db, tenant_id=tenant_id, run_id=run_id)

    assert reservation.execution is winner
    assert reservation.created is False
    assert reservation.can_execute is False
    assert reservation.blocked is True
    assert reservation.reconciliation_required is True
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [IntegrityError]
    assert db.flush_count == 1
    assert "FOR UPDATE" in _sql(db.statements[2])


@pytest.mark.asyncio
async def test_concurrent_winner_with_different_request_fails_closed():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    winner = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    winner.arguments_hash = tool_execution.fingerprint_arguments({"different": True})
    conflict = IntegrityError(
        statement="INSERT INTO agent_tool_executions",
        params={},
        orig=Exception("uq_agent_tool_executions_run_tool_call"),
    )
    db = _FakeSession(run_id, None, winner, flush_errors=(conflict,))

    with pytest.raises(tool_execution.ToolExecutionError) as exc_info:
        await _reserve(db, tenant_id=tenant_id, run_id=run_id)

    assert exc_info.value.code == "tool_call_idempotency_mismatch"


@pytest.mark.asyncio
async def test_terminal_transition_requires_row_lock_and_current_owner():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    db = _FakeSession(execution)

    result = await tool_execution.mark_tool_execution_succeeded(
        db,
        tenant_id=tenant_id,
        execution_id=execution.id,
        lease_owner="worker-1",
        result_summary="sent",
        result_ref="message://42",
        clock=lambda: _NOW,
    )

    assert result is execution
    assert execution.status == "succeeded"
    assert execution.result_summary == "sent"
    assert execution.result_ref == "message://42"
    assert execution.completed_at == _NOW
    assert execution.lease_expires_at is None
    assert db.flush_count == 1
    sql = _sql(db.statements[0])
    assert "agent_tool_executions.tenant_id" in sql
    assert "agent_tool_executions.id" in sql
    assert "FOR UPDATE" in sql

    other = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    wrong_owner_db = _FakeSession(other)
    with pytest.raises(tool_execution.ToolExecutionError) as exc_info:
        await tool_execution.mark_tool_execution_failed(
            wrong_owner_db,
            tenant_id=tenant_id,
            execution_id=other.id,
            lease_owner="worker-2",
            result_summary="failed",
        )
    assert exc_info.value.code == "tool_execution_lease_lost"
    assert wrong_owner_db.flush_count == 0


@pytest.mark.asyncio
async def test_terminal_retry_is_exactly_idempotent_and_cannot_change_status():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    succeeded = _execution(
        tenant_id=tenant_id,
        run_id=run_id,
        status="succeeded",
        result_summary="sent",
        result_ref="message://42",
    )
    exact_db = _FakeSession(succeeded)

    exact = await tool_execution.mark_tool_execution_succeeded(
        exact_db,
        tenant_id=tenant_id,
        execution_id=succeeded.id,
        lease_owner="another-worker",
        result_summary="sent",
        result_ref="message://42",
    )
    assert exact is succeeded
    assert exact_db.flush_count == 0

    conflict_db = _FakeSession(succeeded)
    with pytest.raises(tool_execution.ToolExecutionError) as exc_info:
        await tool_execution.mark_tool_execution_unknown(
            conflict_db,
            tenant_id=tenant_id,
            execution_id=succeeded.id,
            lease_owner="worker-1",
            result_summary="uncertain",
        )
    assert exc_info.value.code == "tool_execution_terminal_conflict"
    assert conflict_db.flush_count == 0


@pytest.mark.asyncio
async def test_unknown_transition_is_durable_and_future_reservation_is_blocked():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    mark_db = _FakeSession(execution)

    await tool_execution.mark_tool_execution_unknown(
        mark_db,
        tenant_id=tenant_id,
        execution_id=execution.id,
        lease_owner="worker-1",
        result_summary="provider timeout after request submission",
        clock=lambda: _NOW,
    )
    assert execution.status == "unknown"

    reserve_db = _FakeSession(run_id, execution)
    decision = await _reserve(reserve_db, tenant_id=tenant_id, run_id=run_id)
    assert decision.blocked is True
    assert decision.reconciliation_required is True
    assert decision.requires_confirmation is True
    assert decision.can_execute is False


@pytest.mark.asyncio
async def test_lease_renewal_never_changes_execution_ownership_or_status():
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    db = _FakeSession(execution)

    renewed = await tool_execution.renew_tool_execution_lease(
        db,
        tenant_id=tenant_id,
        execution_id=execution.id,
        lease_owner="worker-1",
        lease_ttl_seconds=120,
        clock=lambda: _NOW,
    )

    assert renewed.status == "started"
    assert renewed.lease_owner == "worker-1"
    assert renewed.lease_expires_at == datetime(2026, 7, 13, 13, 2, tzinfo=UTC)
    assert db.flush_count == 1


def test_service_never_reads_product_projection_as_execution_state():
    source = inspect.getsource(tool_execution)

    assert "projected_execution_status" not in source
    assert "projected_waiting" not in source
    assert "projected_result" not in source
