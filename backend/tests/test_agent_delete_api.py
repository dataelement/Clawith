import uuid
from datetime import UTC, datetime

import pytest

from app.api import agents as agents_api
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.models.user import User


class DummyResult:
    def __init__(self, values=()):
        self._values = list(values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.added: list[object] = []
        self.executed: list[object] = []
        self.deleted: list[object] = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        self.executed.append(statement)
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)
        raise AssertionError("logical deletion must not call db.delete")

    async def flush(self):
        return None

    async def commit(self):
        self.commit_count += 1


def make_user(**overrides) -> User:
    values = {
        "id": uuid.uuid4(),
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "hashed",
        "display_name": "Alice",
        "role": "org_admin",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return User(**values)


def make_agent(user: User, **overrides) -> Agent:
    values = {
        "id": uuid.uuid4(),
        "name": "Ops Bot",
        "role_description": "assistant",
        "creator_id": user.id,
        "tenant_id": user.tenant_id,
        "status": "idle",
        "agent_type": "native",
    }
    values.update(overrides)
    return Agent(**values)


@pytest.mark.asyncio
async def test_delete_agent_marks_deleted_and_preserves_history(monkeypatch):
    creator = make_user()
    agent = make_agent(creator)
    unfinished_run_id = uuid.uuid4()
    db = RecordingDB(responses=[DummyResult([unfinished_run_id]), DummyResult()])
    cancel_calls: list[dict] = []
    remove_calls: list[uuid.UUID] = []

    async def fake_check_agent_access(_db, _user, _agent_id, *, include_deleted=False):
        assert include_deleted is True
        return agent, "manage"

    async def fake_enqueue_cancel(_db, **kwargs):
        cancel_calls.append(kwargs)

    class FakeAgentManager:
        async def remove_container(self, value):
            remove_calls.append(value.id)
            return True

        async def archive_agent_files(self, _agent_id):
            raise AssertionError("logical deletion must not archive Workspace")

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check_agent_access)
    monkeypatch.setattr(agents_api, "enqueue_cancel", fake_enqueue_cancel, raising=False)
    monkeypatch.setattr(agents_api, "agent_manager", FakeAgentManager(), raising=False)

    await agents_api.delete_agent(agent_id=agent.id, current_user=creator, db=db)

    assert agent.deleted_at is not None
    assert agent.status == "stopped"
    assert db.deleted == []
    assert remove_calls == [agent.id]
    assert [call["run_id"] for call in cancel_calls] == [unfinished_run_id]
    assert cancel_calls[0]["reason"] == "agent_deleted"
    assert any(
        isinstance(value, AuditLog) and value.action == "agent_deleted"
        for value in db.added
    )
    sql = "\n".join(str(statement) for statement in db.executed)
    assert "agent_run_events" in sql
    assert "workspace_edit_locks" in sql
    assert "DELETE FROM audit_logs" not in sql
    assert "UPDATE chat_messages SET agent_id = NULL" not in sql
    assert "DELETE FROM tasks" not in sql


@pytest.mark.asyncio
async def test_delete_agent_is_idempotent_and_retries_runtime_cleanup(monkeypatch):
    creator = make_user()
    deleted_at = datetime.now(UTC)
    agent = make_agent(creator, deleted_at=deleted_at, status="stopped")
    db = RecordingDB(responses=[DummyResult(), DummyResult()])
    remove_calls: list[uuid.UUID] = []

    async def fake_check_agent_access(_db, _user, _agent_id, *, include_deleted=False):
        assert include_deleted is True
        return agent, "manage"

    class FakeAgentManager:
        async def remove_container(self, value):
            remove_calls.append(value.id)
            return False

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check_agent_access)
    monkeypatch.setattr(agents_api, "agent_manager", FakeAgentManager(), raising=False)

    await agents_api.delete_agent(agent_id=agent.id, current_user=creator, db=db)

    assert agent.deleted_at == deleted_at
    assert not any(isinstance(value, AuditLog) for value in db.added)
    assert remove_calls == [agent.id]


@pytest.mark.asyncio
async def test_delete_agent_keeps_logical_delete_when_container_removal_fails(monkeypatch):
    creator = make_user()
    agent = make_agent(creator)
    db = RecordingDB(responses=[DummyResult(), DummyResult()])

    async def fake_check_agent_access(_db, _user, _agent_id, *, include_deleted=False):
        assert include_deleted is True
        return agent, "manage"

    class FailingAgentManager:
        async def remove_container(self, _agent):
            raise RuntimeError("docker unavailable")

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check_agent_access)
    monkeypatch.setattr(agents_api, "agent_manager", FailingAgentManager(), raising=False)

    await agents_api.delete_agent(agent_id=agent.id, current_user=creator, db=db)

    assert agent.deleted_at is not None
    assert agent.status == "stopped"
    assert db.commit_count >= 1
