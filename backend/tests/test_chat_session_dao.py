"""Sandbox authorization contracts for ChatSessionDAO."""

from collections import deque
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.dao.chat_session_dao import chat_session_dao


class _Result:
    def __init__(self, values=None) -> None:
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class _RecordingDB:
    def __init__(self, *results: _Result) -> None:
        self.results = deque(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.popleft()


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _session(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    session_type: str,
    group_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_type=session_type,
        group_id=group_id,
        deleted_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("session_type", ["direct", "group"])
async def test_sandbox_scope_preserves_exact_agent_ownership(session_type: str) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    chat_session = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_type=session_type,
    )
    db = _RecordingDB(_Result([chat_session]))

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=chat_session.id,
        db=db,
    )

    assert result is chat_session
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_sandbox_scope_rejects_session_owned_by_another_agent() -> None:
    tenant_id = uuid.uuid4()
    chat_session = _session(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        session_type="direct",
    )
    db = _RecordingDB(_Result([chat_session]))

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        session_id=chat_session.id,
        db=db,
    )

    assert result is None
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_sandbox_scope_allows_active_native_group_agent_member() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    chat_session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        session_type="group",
        group_id=uuid.uuid4(),
    )
    db = _RecordingDB(_Result([chat_session]), _Result([uuid.uuid4()]))

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=chat_session.id,
        db=db,
    )

    assert result is chat_session
    assert len(db.statements) == 2
    membership_sql = _sql(db.statements[1])
    assert "JOIN groups ON groups.id = group_members.group_id" in membership_sql
    assert "JOIN participants ON participants.id = group_members.participant_id" in membership_sql
    assert f"groups.tenant_id = '{tenant_id}'" in membership_sql
    assert "groups.deleted_at IS NULL" in membership_sql
    assert "group_members.removed_at IS NULL" in membership_sql
    assert "participants.type = 'agent'" in membership_sql
    assert f"participants.ref_id = '{agent_id}'" in membership_sql


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["removed member", "deleted group", "cross-tenant group"])
async def test_sandbox_scope_rejects_inactive_native_group_membership(reason: str) -> None:
    tenant_id = uuid.uuid4()
    chat_session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        session_type="group",
        group_id=uuid.uuid4(),
    )
    db = _RecordingDB(_Result([chat_session]), _Result())

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        session_id=chat_session.id,
        db=db,
    )

    assert result is None, reason


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["deleted session", "cross-tenant session"])
async def test_sandbox_scope_rejects_inaccessible_session(reason: str) -> None:
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    db = _RecordingDB(_Result())

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        session_id=session_id,
        db=db,
    )

    assert result is None, reason
    session_sql = _sql(db.statements[0])
    assert f"chat_sessions.tenant_id = '{tenant_id}'" in session_sql
    assert f"chat_sessions.id = '{session_id}'" in session_sql
    assert "chat_sessions.deleted_at IS NULL" in session_sql


@pytest.mark.asyncio
async def test_sandbox_scope_rejects_malformed_owned_native_group_session() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    chat_session = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_type="group",
        group_id=uuid.uuid4(),
    )
    db = _RecordingDB(_Result([chat_session]))

    result = await chat_session_dao.get_active_for_sandbox_agent(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=chat_session.id,
        db=db,
    )

    assert result is None
    assert len(db.statements) == 1
