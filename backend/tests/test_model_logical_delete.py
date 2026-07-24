import uuid
from datetime import UTC, datetime

import pytest

from app.api import enterprise as enterprise_api
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.models.llm import LLMModel
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

    async def commit(self):
        self.commit_count += 1


def make_user(**overrides) -> User:
    values = {
        "id": uuid.uuid4(),
        "username": "admin",
        "email": "admin@example.com",
        "password_hash": "hashed",
        "display_name": "Admin",
        "role": "org_admin",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return User(**values)


def make_model(user: User, **overrides) -> LLMModel:
    values = {
        "id": uuid.uuid4(),
        "tenant_id": user.tenant_id,
        "provider": "openai",
        "model": "gpt-test",
        "api_key_encrypted": "encrypted",
        "label": "Test model",
        "enabled": True,
    }
    values.update(overrides)
    return LLMModel(**values)


def test_agent_and_model_define_logical_delete_columns():
    assert "deleted_at" in Agent.__table__.columns
    assert "deleted_at" in LLMModel.__table__.columns


@pytest.mark.asyncio
async def test_delete_model_marks_unavailable_without_clearing_references():
    user = make_user()
    model = make_model(user)
    db = RecordingDB(responses=[DummyResult([model])])

    await enterprise_api.remove_llm_model(
        model_id=model.id,
        current_user=user,
        db=db,
    )

    assert model.deleted_at is not None
    assert model.enabled is False
    assert db.deleted == []
    assert any(
        isinstance(value, AuditLog) and value.action == "llm_model_deleted"
        for value in db.added
    )
    sql = "\n".join(str(statement) for statement in db.executed)
    assert "UPDATE agents SET primary_model_id" not in sql
    assert "UPDATE agents SET fallback_model_id" not in sql


@pytest.mark.asyncio
async def test_delete_model_is_idempotent():
    user = make_user()
    deleted_at = datetime.now(UTC)
    model = make_model(user, deleted_at=deleted_at, enabled=False)
    db = RecordingDB(responses=[DummyResult([model])])

    await enterprise_api.remove_llm_model(
        model_id=model.id,
        current_user=user,
        db=db,
    )

    assert model.deleted_at == deleted_at
    assert db.deleted == []
    assert not any(isinstance(value, AuditLog) for value in db.added)
    assert db.commit_count == 0
