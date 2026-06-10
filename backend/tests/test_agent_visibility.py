import uuid
from types import SimpleNamespace

import pytest

from app.core import permissions
from app.core.permissions import build_visible_agents_query


def make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "role": "member",
        "tenant_id": uuid.uuid4(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_visible_agents_query_restricts_to_same_tenant_and_visible_permissions():
    user = make_user()

    stmt = build_visible_agents_query(user)
    sql = str(stmt)

    assert "agents.tenant_id" in sql
    assert "agents.creator_id" in sql
    assert "agent_permissions.scope_type" in sql
    assert "agent_permissions.scope_id" in sql


def test_build_visible_agents_query_platform_admin_still_uses_visibility_filters():
    admin = make_user(role="platform_admin", tenant_id=None)

    sql = str(build_visible_agents_query(admin, tenant_id=uuid.uuid4()))

    assert "agents.tenant_id" in sql
    assert "agents.access_mode" in sql


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RelationshipStatusDb:
    def __init__(self, source):
        self.source = source

    async def execute(self, _stmt):
        return _ScalarResult(self.source)


@pytest.mark.asyncio
async def test_agent_relationship_status_requires_original_creator_to_still_manage_both_agents(monkeypatch):
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        access_mode="company",
        status="ready",
        expires_at=None,
    )
    target = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        access_mode="company",
        status="ready",
        expires_at=None,
    )
    rel = SimpleNamespace(
        agent_id=source.id,
        target_agent_id=target.id,
        target_agent=target,
        created_by_user_id=creator_id,
    )

    async def cannot_manage(_db, _user_id, _agent):
        return False

    monkeypatch.setattr(permissions, "user_can_manage_agent_id", cannot_manage)

    status = await permissions.evaluate_agent_relationship_status(
        _RelationshipStatusDb(source),
        rel,
        current_user_id=uuid.uuid4(),
    )

    assert status["access_allowed"] is False
    assert status["access_status"] == "restricted"
    assert status["access_status_reason"] == "relationship_creator_no_longer_manages_both_agents"


@pytest.mark.asyncio
async def test_agent_relationship_status_active_when_original_creator_still_manages_both_agents(monkeypatch):
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        access_mode="custom",
        status="ready",
        expires_at=None,
    )
    target = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        access_mode="private",
        status="ready",
        expires_at=None,
    )
    rel = SimpleNamespace(
        agent_id=source.id,
        target_agent_id=target.id,
        target_agent=target,
        created_by_user_id=creator_id,
    )

    async def can_manage(_db, user_id, _agent):
        return user_id == creator_id

    monkeypatch.setattr(permissions, "user_can_manage_agent_id", can_manage)

    status = await permissions.evaluate_agent_relationship_status(
        _RelationshipStatusDb(source),
        rel,
    )

    assert status["access_allowed"] is True
    assert status["access_status"] == "active"
