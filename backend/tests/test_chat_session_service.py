"""Focused tests for Direct Chat primary, deletion, and Runtime cancellation rules."""

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.services import chat_session_service


class DummyResult:
    def __init__(self, values=None):
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class RecordingDB:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.popleft()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1

    async def commit(self):
        raise AssertionError("service must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("service must not roll back the caller transaction")


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _scope():
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _session(
    tenant_id,
    agent_id,
    user_id,
    participant_id,
    *,
    is_primary,
):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=participant_id,
        session_type="direct",
        source_channel="web",
        title="Session",
        is_primary=is_primary,
        deleted_at=None,
        updated_at=now,
        created_at=now,
        last_message_at=now,
    )


@pytest.mark.asyncio
async def test_ensure_primary_uses_transaction_lock_and_reuses_active_primary():
    tenant_id, agent_id, user_id, participant_id = _scope()
    primary = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=True,
    )
    db = RecordingDB(DummyResult(), DummyResult([primary]))

    result = await chat_session_service.ensure_primary_direct_session(
        db,
        tenant_id,
        agent_id,
        user_id,
        participant_id,
    )

    assert result is primary
    assert db.added == []
    assert db.flush_count == 0
    lock_sql = _sql(db.statements[0])
    assert "pg_advisory_xact_lock" in lock_sql
    assert "hashtextextended" in lock_sql
    assert str(tenant_id) in lock_sql
    primary_sql = _sql(db.statements[1])
    assert f"chat_sessions.tenant_id = '{tenant_id}'" in primary_sql
    assert "chat_sessions.session_type = 'direct'" in primary_sql
    assert "chat_sessions.deleted_at IS NULL" in primary_sql
    assert "chat_sessions.is_primary IS true" in primary_sql


@pytest.mark.asyncio
async def test_ensure_primary_promotes_best_active_session():
    tenant_id, agent_id, user_id, participant_id = _scope()
    existing = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=False,
    )
    db = RecordingDB(DummyResult(), DummyResult(), DummyResult([existing]))

    result = await chat_session_service.ensure_primary_direct_session(
        db,
        tenant_id,
        agent_id,
        user_id,
        participant_id,
    )

    assert result is existing
    assert existing.is_primary is True
    assert db.flush_count == 1
    replacement_sql = _sql(db.statements[2])
    assert "chat_sessions.last_message_at DESC NULLS LAST" in replacement_sql
    assert "chat_sessions.created_at DESC" in replacement_sql
    assert "chat_sessions.id DESC" in replacement_sql


@pytest.mark.asyncio
async def test_first_created_direct_session_is_primary_and_later_session_is_side_session():
    tenant_id, agent_id, user_id, participant_id = _scope()
    first_db = RecordingDB(DummyResult(), DummyResult(), DummyResult())

    first = await chat_session_service.create_direct_session(
        first_db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=participant_id,
        title="First",
    )

    assert first.is_primary is True
    assert first.tenant_id == tenant_id
    assert first.session_type == "direct"
    assert first.created_by_participant_id == participant_id
    assert first_db.added == [first]

    primary = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=True,
    )
    later_db = RecordingDB(DummyResult(), DummyResult([primary]))
    later = await chat_session_service.create_direct_session(
        later_db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=participant_id,
        title="Side topic",
    )

    assert later.is_primary is False
    assert later_db.added == [later]


@pytest.mark.asyncio
async def test_soft_delete_primary_promotes_replacement_and_cancels_only_collaboration(
    monkeypatch,
):
    tenant_id, agent_id, user_id, participant_id = _scope()
    session = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=True,
    )
    replacement = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=False,
    )
    foreground = SimpleNamespace(id=uuid.uuid4())
    orchestration = SimpleNamespace(id=uuid.uuid4())
    delegated = SimpleNamespace(id=uuid.uuid4())
    db = RecordingDB(
        DummyResult(),
        DummyResult([session]),
        DummyResult([replacement]),
        DummyResult([foreground, orchestration, delegated]),
    )
    cancel_calls = []

    async def fake_enqueue_cancel(_db, **kwargs):
        cancel_calls.append(kwargs)

    monkeypatch.setattr(chat_session_service, "enqueue_cancel", fake_enqueue_cancel)

    result = await chat_session_service.soft_delete_direct_session(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session.id,
        actor_user_id=user_id,
    )

    assert result is not None
    assert result.replacement is replacement
    assert result.cancelled_run_ids == (
        foreground.id,
        orchestration.id,
        delegated.id,
    )
    assert session.deleted_at is not None
    assert session.is_primary is True
    assert replacement.is_primary is True
    assert [call["run_id"] for call in cancel_calls] == [
        foreground.id,
        orchestration.id,
        delegated.id,
    ]
    assert all(call["reason"] == "session_deleted" for call in cancel_calls)
    assert all(
        call["idempotency_key"]
        == f"session-delete:{session.id}:run:{call['run_id']}"
        for call in cancel_calls
    )

    cancellation_sql = _sql(db.statements[3])
    assert "agent_runs.run_kind IN ('foreground', 'orchestration')" in cancellation_sql
    assert "agent_runs.run_kind = 'delegated'" in cancellation_sql
    assert "background" not in cancellation_sql
    assert "agent_runs.projected_execution_status =" not in cancellation_sql
    assert "agent_runs.projected_execution_status IN" not in cancellation_sql
    assert all(statement.__class__.__name__ != "Delete" for statement in db.statements)


@pytest.mark.asyncio
async def test_soft_delete_nonprimary_does_not_run_replacement_election(monkeypatch):
    tenant_id, agent_id, user_id, participant_id = _scope()
    session = _session(
        tenant_id,
        agent_id,
        user_id,
        participant_id,
        is_primary=False,
    )
    db = RecordingDB(DummyResult(), DummyResult([session]), DummyResult())

    async def fake_enqueue_cancel(_db, **kwargs):
        raise AssertionError(f"unexpected cancellation: {kwargs}")

    monkeypatch.setattr(chat_session_service, "enqueue_cancel", fake_enqueue_cancel)

    result = await chat_session_service.soft_delete_direct_session(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session.id,
        actor_user_id=user_id,
    )

    assert result is not None
    assert result.replacement is None
    assert result.cancelled_run_ids == ()
    assert len(db.statements) == 3
