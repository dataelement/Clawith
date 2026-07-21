"""Focused unit tests for Runtime registry and command inbox persistence."""

from collections import deque
from datetime import UTC, datetime
import inspect
import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime import persistence


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
        raise AssertionError("persistence helpers must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("persistence helpers must not roll back the caller transaction")


def _registration(**overrides) -> persistence.RunRegistration:
    values = {
        "tenant_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
        "source_type": "chat",
        "source_id": str(uuid.uuid4()),
        "source_execution_id": f"group_mention:{uuid.uuid4()}:agent:{uuid.uuid4()}",
        "goal": "Answer the current message",
        "run_kind": "foreground",
        "runtime_type": "langgraph",
        "model_id": uuid.uuid4(),
        "model_turn_limit": 50,
        "graph_name": "clawith_agent_runtime",
        "graph_version": "v1",
        "delivery_status": "pending",
        "delivery_target": {"kind": "session"},
    }
    values.update(overrides)
    return persistence.RunRegistration(**values)


def _existing_run(registration: persistence.RunRegistration) -> AgentRun:
    run_id = uuid.uuid4()
    return AgentRun(
        id=run_id,
        runtime_thread_id=registration.runtime_thread_id or str(run_id),
        lane_held=False,
        delivery_status=registration.delivery_status,
        **persistence._registration_values(registration),
    )


def _command(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    command_type: str = "resume",
    payload: dict | None = None,
    idempotency_key: str = "resume:1",
    status: str = "pending",
    claimant: str | None = None,
    attempt_count: int = 0,
    created_at: datetime | None = None,
) -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        command_type=command_type,
        payload=payload or {},
        idempotency_key=idempotency_key,
        status=status,
        claimed_by=claimant,
        attempt_count=attempt_count,
        created_at=created_at or datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_register_run_and_start_command_share_the_caller_transaction():
    registration = _registration()
    db = _FakeSession(None)

    result = await persistence.register_run_with_start(
        db,
        registration,
        start_payload={"input_message_id": registration.source_id},
        start_idempotency_key="start:message",
        actor_user_id=uuid.uuid4(),
    )

    assert result.created is True
    assert db.added[:2] == [result.run, result.start_command]
    assert len(db.added) == 3
    created_event = db.added[2]
    assert created_event.event_type == "run_created"
    assert created_event.run_id == result.run.id
    assert created_event.payload["thread_id"] == str(result.run.id)
    assert db.flush_count == 1
    assert result.run.id == result.start_command.run_id
    assert result.run.runtime_thread_id == str(result.run.id)
    assert result.run.model_turn_limit == 50
    assert result.run.lane_held is False
    assert result.start_command.command_type == "start"
    assert result.start_command.status == "pending"
    assert result.start_command.attempt_count == 0
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [None]


@pytest.mark.asyncio
async def test_registration_preserves_an_explicit_shared_thread_identity():
    thread_id = str(uuid.uuid4())
    registration = _registration(runtime_thread_id=thread_id)
    db = _FakeSession(None)

    result = await persistence.register_run_with_start(
        db,
        registration,
        start_payload={"input_message_id": registration.source_id},
        start_idempotency_key="start:shared-thread",
    )

    assert result.run.runtime_thread_id == thread_id
    assert result.run.id != uuid.UUID(thread_id)


@pytest.mark.asyncio
async def test_source_retry_returns_the_exact_existing_run_and_start_command():
    registration = _registration()
    run = _existing_run(registration)
    actor_user_id = uuid.uuid4()
    command = _command(
        tenant_id=registration.tenant_id,
        run_id=run.id,
        command_type="start",
        payload={"input_message_id": registration.source_id},
        idempotency_key="start:message",
    )
    command.actor_user_id = actor_user_id
    db = _FakeSession(run, command)

    result = await persistence.register_run_with_start(
        db,
        registration,
        start_payload={"input_message_id": registration.source_id},
        start_idempotency_key="start:message",
        actor_user_id=actor_user_id,
    )

    assert result == persistence.RegisteredRun(run=run, start_command=command, created=False)
    assert db.added == []
    assert db.flush_count == 0
    assert db.nested_entries == 0


@pytest.mark.asyncio
async def test_source_retry_rejects_different_immutable_inputs():
    original = _registration()
    existing = _existing_run(original)
    retry = _registration(
        tenant_id=original.tenant_id,
        agent_id=original.agent_id,
        source_id=original.source_id,
        source_execution_id=original.source_execution_id,
        model_id=original.model_id,
        goal="A different goal",
    )
    db = _FakeSession(existing)

    with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
        await persistence.register_run_with_start(
            db,
            retry,
            start_payload={},
            start_idempotency_key="start:message",
        )

    assert exc_info.value.code == "source_idempotency_mismatch"
    assert "goal" in str(exc_info.value)
    assert db.added == []


@pytest.mark.asyncio
async def test_source_retry_ignores_mutated_delivery_status():
    registration = _registration(delivery_status="pending")
    existing = _existing_run(registration)
    existing.delivery_status = "delivered"
    command = _command(
        tenant_id=registration.tenant_id,
        run_id=existing.id,
        command_type="start",
        payload={},
        idempotency_key="start:message",
    )
    db = _FakeSession(existing, command)

    result = await persistence.register_run_with_start(
        db,
        registration,
        start_payload={},
        start_idempotency_key="start:message",
    )

    assert result == persistence.RegisteredRun(
        run=existing,
        start_command=command,
        created=False,
    )
    assert existing.delivery_status == "delivered"


@pytest.mark.asyncio
async def test_concurrent_source_insert_uses_savepoint_and_reuses_exact_winner():
    registration = _registration()
    winner = _existing_run(registration)
    winner_command = _command(
        tenant_id=registration.tenant_id,
        run_id=winner.id,
        command_type="start",
        payload={"input_message_id": registration.source_id},
        idempotency_key="start:message",
    )
    conflict = IntegrityError(
        statement="INSERT INTO agent_runs",
        params={},
        orig=Exception("uq_agent_runs_source_execution"),
    )
    db = _FakeSession(
        None,
        winner,
        winner_command,
        flush_errors=(conflict,),
    )

    result = await persistence.register_run_with_start(
        db,
        registration,
        start_payload={"input_message_id": registration.source_id},
        start_idempotency_key="start:message",
    )

    assert result == persistence.RegisteredRun(
        run=winner,
        start_command=winner_command,
        created=False,
    )
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [IntegrityError]
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_concurrent_source_without_start_command_fails_closed():
    registration = _registration()
    winner = _existing_run(registration)
    conflict = IntegrityError(
        statement="INSERT INTO agent_runs",
        params={},
        orig=Exception("uq_agent_runs_source_execution"),
    )
    db = _FakeSession(None, winner, None, flush_errors=(conflict,))

    with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
        await persistence.register_run_with_start(
            db,
            registration,
            start_payload={},
            start_idempotency_key="start:message",
        )

    assert exc_info.value.code == "source_retry_missing_start_command"
    assert db.nested_exit_exceptions == [IntegrityError]


@pytest.mark.asyncio
async def test_resume_and_cancel_are_idempotent_without_reading_run_projection():
    registration = _registration()
    run = _existing_run(registration)
    db = _FakeSession(run, None)

    resume = await persistence.enqueue_resume(
        db,
        tenant_id=registration.tenant_id,
        run_id=run.id,
        payload={"resume_type": "user_input", "value": "continue"},
        idempotency_key="resume:message:2",
    )

    assert resume.created is True
    assert resume.command.command_type == "resume"
    assert db.flush_count == 1
    assert db.nested_entries == 1

    cancel_db = _FakeSession(run, None)
    cancel = await persistence.enqueue_cancel(
        cancel_db,
        tenant_id=registration.tenant_id,
        run_id=run.id,
        reason="user_abort",
        idempotency_key="cancel:user:1",
    )
    assert cancel.command.command_type == "cancel"
    assert cancel.command.payload == {"reason": "user_abort"}
    assert cancel_db.nested_entries == 1

    source = inspect.getsource(persistence)
    assert "projected_execution_status" not in source
    assert "command_seq" not in source


@pytest.mark.asyncio
async def test_command_idempotency_key_rejects_a_different_resume_payload():
    registration = _registration()
    run = _existing_run(registration)
    existing = _command(
        tenant_id=registration.tenant_id,
        run_id=run.id,
        payload={"value": "original"},
        idempotency_key="resume:1",
    )
    db = _FakeSession(run, existing)

    with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
        await persistence.enqueue_resume(
            db,
            tenant_id=registration.tenant_id,
            run_id=run.id,
            payload={"value": "changed"},
            idempotency_key="resume:1",
        )

    assert exc_info.value.code == "command_idempotency_mismatch"
    assert db.added == []


@pytest.mark.asyncio
async def test_concurrent_command_insert_uses_savepoint_and_reuses_exact_winner():
    registration = _registration()
    run = _existing_run(registration)
    winner = _command(
        tenant_id=registration.tenant_id,
        run_id=run.id,
        payload={"value": "continue"},
        idempotency_key="resume:1",
    )
    conflict = IntegrityError(
        statement="INSERT INTO agent_run_commands",
        params={},
        orig=Exception("uq_agent_run_commands_run_idempotency"),
    )
    db = _FakeSession(run, None, winner, flush_errors=(conflict,))

    result = await persistence.enqueue_resume(
        db,
        tenant_id=registration.tenant_id,
        run_id=run.id,
        payload={"value": "continue"},
        idempotency_key="resume:1",
    )

    assert result == persistence.EnqueuedCommand(command=winner, created=False)
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [IntegrityError]
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_fifo_without_consuming_execution_attempt():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    command = _command(tenant_id=uuid.uuid4(), run_id=uuid.uuid4(), attempt_count=2)
    db = _FakeSession(command)

    claimed = await persistence.claim_next_command(
        db,
        claimant="worker-1",
        claim_ttl_seconds=60,
        max_attempts=5,
        clock=lambda: now,
    )

    assert claimed is command
    assert command.status == "claimed"
    assert command.claimed_by == "worker-1"
    assert command.attempt_count == 2
    assert command.claim_expires_at == datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    assert db.flush_count == 1

    sql = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY agent_run_commands.created_at, agent_run_commands.id" in sql
    assert "previous_command.run_id = agent_run_commands.run_id" in sql
    assert (
        "(previous_command.created_at, previous_command.id) < (agent_run_commands.created_at, agent_run_commands.id)"
    ) in sql
    assert "previous_command.status IN ('pending', 'claimed')" in sql
    assert "agent_run_commands.attempt_count < 5" not in sql


@pytest.mark.asyncio
async def test_start_claim_acquires_only_the_earliest_free_scheduling_lane():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    position_id = uuid.uuid4()
    run = _existing_run(
        _registration(
            tenant_id=tenant_id,
            agent_id=agent_id,
            scheduling_lane_key=f"group_mention:{tenant_id}:{agent_id}",
            scheduling_position_created_at=now,
            scheduling_position_id=position_id,
        )
    )
    command = _command(
        tenant_id=tenant_id,
        run_id=run.id,
        command_type="start",
    )
    db = _FakeSession(command, run, None)

    claimed = await persistence.claim_next_command(
        db,
        claimant="worker-1",
        claim_ttl_seconds=60,
        max_attempts=5,
        clock=lambda: now,
    )

    assert claimed is command
    assert run.lane_held is True
    assert run.lane_claimed_at == now
    assert command.status == "claimed"
    assert db.flush_count == 1

    sql = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "candidate_run.scheduling_lane_key" in sql
    assert "lane_holder.lane_held IS true" in sql
    assert "earlier_lane_command.command_type = 'start'" in sql
    assert (
        "earlier_lane_run.scheduling_position_created_at, "
        "earlier_lane_run.scheduling_position_id" in sql
    )
    assert "earlier_lane_run.created_at, earlier_lane_run.id" in sql


@pytest.mark.asyncio
async def test_start_claim_leaves_command_pending_when_lane_becomes_busy():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run = _existing_run(
        _registration(
            tenant_id=tenant_id,
            agent_id=agent_id,
            scheduling_lane_key=f"group_mention:{tenant_id}:{agent_id}",
            scheduling_position_created_at=now,
            scheduling_position_id=uuid.uuid4(),
        )
    )
    command = _command(
        tenant_id=tenant_id,
        run_id=run.id,
        command_type="start",
    )
    db = _FakeSession(command, run, uuid.uuid4())

    claimed = await persistence.claim_next_command(
        db,
        claimant="worker-1",
        claim_ttl_seconds=60,
        max_attempts=5,
        clock=lambda: now,
    )

    assert claimed is None
    assert run.lane_held is False
    assert command.status == "pending"
    assert command.claimed_by is None
    assert command.attempt_count == 0
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_claim_makes_exhausted_command_visible_for_explicit_quarantine():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    command = _command(
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="dead-worker",
        attempt_count=5,
    )
    db = _FakeSession(command)

    claimed = await persistence.claim_next_command(
        db,
        claimant="worker-2",
        claim_ttl_seconds=60,
        max_attempts=5,
        clock=lambda: now,
    )

    assert claimed is command
    assert command.status == "claimed"
    assert command.claimed_by == "worker-2"
    assert command.attempt_count == 5
    assert command.error_code is None
    assert command.applied_at is None
    assert db.flush_count == 1
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_begin_command_attempt_increments_only_after_thread_lock_boundary():
    tenant_id = uuid.uuid4()
    command = _command(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="worker-1",
        attempt_count=2,
    )
    db = _FakeSession(command)

    started = await persistence.begin_command_attempt(
        db,
        tenant_id=tenant_id,
        command_id=command.id,
        claimant="worker-1",
        max_attempts=5,
    )

    assert started is command
    assert command.attempt_count == 3
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_exhausted_start_is_claimed_for_quarantine_without_holding_lane():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run = _existing_run(
        _registration(
            tenant_id=tenant_id,
            agent_id=agent_id,
            scheduling_lane_key=f"group_mention:{tenant_id}:{agent_id}",
            scheduling_position_created_at=now,
            scheduling_position_id=uuid.uuid4(),
        )
    )
    command = _command(
        tenant_id=tenant_id,
        run_id=run.id,
        command_type="start",
        attempt_count=5,
    )
    db = _FakeSession(command, run, None)

    claimed = await persistence.claim_next_command(
        db,
        claimant="worker-1",
        claim_ttl_seconds=60,
        max_attempts=5,
        clock=lambda: now,
    )

    assert claimed is command
    assert run.lane_held is False
    assert command.attempt_count == 5


@pytest.mark.asyncio
async def test_rejecting_start_atomically_releases_held_lane():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    run = _existing_run(
        _registration(
            tenant_id=tenant_id,
            scheduling_lane_key=f"group_mention:{tenant_id}:{uuid.uuid4()}",
            scheduling_position_created_at=now,
            scheduling_position_id=uuid.uuid4(),
        )
    )
    run.lane_held = True
    run.lane_claimed_at = now
    command = _command(
        tenant_id=tenant_id,
        run_id=run.id,
        command_type="start",
        status="claimed",
        claimant="worker-1",
        attempt_count=5,
    )
    db = _FakeSession(command, run)

    rejected = await persistence.mark_command_rejected(
        db,
        tenant_id=tenant_id,
        command_id=command.id,
        claimant="worker-1",
        error_code="reconciliation_required",
        clock=lambda: now,
    )

    assert rejected.status == "rejected"
    assert rejected.error_code == "reconciliation_required"
    assert run.lane_held is False
    assert run.lane_claimed_at is None
    assert db.flush_count == 1


def test_rejected_start_lane_repair_targets_only_abandoned_holders() -> None:
    statement = persistence._release_rejected_start_lanes_statement()
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "UPDATE agent_runs SET lane_held=false, lane_claimed_at=NULL" in sql
    assert "agent_runs.lane_held IS true" in sql
    assert "agent_run_commands.command_type = 'start'" in sql
    assert "agent_run_commands.status = 'rejected'" in sql
    assert "agent_run_commands.run_id = agent_runs.id" in sql


@pytest.mark.asyncio
async def test_applied_and_rejected_transitions_require_the_current_claimant():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    command = _command(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="worker-1",
    )
    db = _FakeSession(command)

    applied = await persistence.mark_command_applied(
        db,
        tenant_id=tenant_id,
        command_id=command.id,
        claimant="worker-1",
        applied_checkpoint_id="checkpoint-1",
        clock=lambda: now,
    )
    assert applied.status == "applied"
    assert applied.applied_checkpoint_id == "checkpoint-1"
    assert applied.applied_at == now
    assert applied.claim_expires_at is None

    other = _command(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="worker-1",
    )
    wrong_claimant_db = _FakeSession(other)
    with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
        await persistence.mark_command_rejected(
            wrong_claimant_db,
            tenant_id=tenant_id,
            command_id=other.id,
            claimant="worker-2",
            error_code="invalid_resume",
            clock=lambda: now,
        )
    assert exc_info.value.code == "command_claim_lost"
    assert wrong_claimant_db.flush_count == 0


@pytest.mark.asyncio
async def test_claim_renewal_and_retry_release_require_the_current_claimant():
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    command = _command(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="worker-1",
        attempt_count=2,
    )
    db = _FakeSession(command, command)

    renewed = await persistence.renew_command_claim(
        db,
        tenant_id=tenant_id,
        command_id=command.id,
        claimant="worker-1",
        claim_ttl_seconds=60,
        clock=lambda: now,
    )
    assert renewed.claim_expires_at == datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    assert renewed.status == "claimed"
    assert renewed.attempt_count == 2

    released = await persistence.release_command_claim(
        db,
        tenant_id=tenant_id,
        command_id=command.id,
        claimant="worker-1",
        error_code="thread_lock_busy",
    )
    assert released.status == "pending"
    assert released.claimed_by is None
    assert released.claim_expires_at is None
    assert released.error_code == "thread_lock_busy"
    assert released.attempt_count == 2
    assert db.flush_count == 2

    lost = _command(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        status="claimed",
        claimant="other-worker",
    )
    with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
        await persistence.renew_command_claim(
            _FakeSession(lost),
            tenant_id=tenant_id,
            command_id=lost.id,
            claimant="worker-1",
            claim_ttl_seconds=60,
            clock=lambda: now,
        )
    assert exc_info.value.code == "command_claim_lost"


@pytest.mark.asyncio
async def test_registration_validates_orchestration_and_lane_invariants_before_io():
    invalid_orchestration = _registration(
        agent_id=uuid.uuid4(),
        run_kind="orchestration",
        system_role="group_planning",
    )
    invalid_lane = _registration(scheduling_lane_key="group_mention:tenant:agent")

    for registration in (invalid_orchestration, invalid_lane):
        db = _FakeSession()
        with pytest.raises(persistence.RuntimePersistenceError) as exc_info:
            await persistence.register_run_with_start(
                db,
                registration,
                start_payload={},
                start_idempotency_key="start:1",
            )
        assert exc_info.value.code == "invalid_runtime_input"
        assert db.statements == []
        assert db.added == []
