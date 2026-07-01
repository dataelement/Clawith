import uuid
from types import SimpleNamespace

import pytest

from app.core import permissions
from app.core.permissions import build_visible_agents_query
from app.services.access_relationships import ensure_access_granted_platform_relationships


def make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "role": "member",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "access_mode": "company",
        "status": "running",
        "is_expired": False,
        "expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_visible_agents_query_restricts_to_same_tenant_and_non_private_agents():
    user = make_user()

    stmt = build_visible_agents_query(user)
    sql = str(stmt)

    assert "agents.tenant_id" in sql
    assert "agents.creator_id" in sql
    assert "agents.access_mode" in sql
    assert "agent_permissions" in sql


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


class _NoExecuteDb:
    async def execute(self, _stmt):
        raise AssertionError("execute() should not be called")


@pytest.mark.asyncio
async def test_custom_agents_do_not_materialize_company_wide_legacy_relationships():
    agent = make_agent(access_mode="custom", tenant_id=uuid.uuid4())

    changed = await ensure_access_granted_platform_relationships(
        _NoExecuteDb(),
        agent,
        created_by_user_id=uuid.uuid4(),
    )

    assert changed is False


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


def test_can_use_agent_static_does_not_grant_custom_without_db_permission():
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)

    assert permissions.can_use_agent_static(user, make_agent(tenant_id=tenant_id, access_mode="company")) is True
    assert permissions.can_use_agent_static(user, make_agent(tenant_id=tenant_id, access_mode="custom")) is False
    assert permissions.can_use_agent_static(user, make_agent(tenant_id=tenant_id, access_mode="private")) is False


def test_can_use_agent_static_keeps_private_creator_only():
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    user = make_user(id=creator_id, tenant_id=tenant_id)
    admin = make_user(role="org_admin", tenant_id=tenant_id)
    private_agent = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="private")

    assert permissions.can_use_agent_static(user, private_agent) is True
    assert permissions.can_use_agent_static(admin, private_agent) is False


def test_evaluate_roster_agent_visibility_matches_phase1_rules():
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    source = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="company")
    custom_target = make_agent(tenant_id=tenant_id, access_mode="custom")
    private_target = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="private")

    custom_visibility = permissions.evaluate_roster_agent_visibility(source, custom_target)
    assert custom_visibility.visible is False
    assert custom_visibility.can_contact is False

    authorized_custom_visibility = permissions.evaluate_roster_agent_visibility(
        source,
        custom_target,
        authorized_custom_target=True,
    )
    assert authorized_custom_visibility.visible is True
    assert authorized_custom_visibility.can_contact is True

    private_visibility = permissions.evaluate_roster_agent_visibility(source, private_target)
    assert private_visibility.visible is False
    assert private_visibility.can_contact is False


def test_evaluate_roster_human_visibility_limits_custom_to_authorized_members():
    tenant_id = uuid.uuid4()
    source = make_agent(tenant_id=tenant_id, access_mode="custom")
    member = SimpleNamespace(tenant_id=tenant_id, user_id=uuid.uuid4(), status="active")

    custom_visibility = permissions.evaluate_roster_human_visibility(source, member)
    assert custom_visibility.visible is False
    assert custom_visibility.can_contact is False

    authorized_custom_visibility = permissions.evaluate_roster_human_visibility(
        source,
        member,
        authorized_custom_human=True,
    )
    assert authorized_custom_visibility.visible is True
    assert authorized_custom_visibility.can_contact is True


def test_evaluate_roster_agent_visibility_allows_same_creator_private_only():
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    source = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="private")
    same_creator_private = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="private")
    other_private = make_agent(tenant_id=tenant_id, creator_id=uuid.uuid4(), access_mode="private")
    company_agent = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="company")

    assert permissions.evaluate_roster_agent_visibility(source, same_creator_private).visible is True
    assert permissions.evaluate_roster_agent_visibility(source, other_private).visible is False
    assert permissions.evaluate_roster_agent_visibility(source, company_agent).visible is False


def test_evaluate_roster_agent_visibility_reports_uncontactable_reason():
    tenant_id = uuid.uuid4()
    source = make_agent(tenant_id=tenant_id, access_mode="company")
    stopped_target = make_agent(tenant_id=tenant_id, access_mode="company", status="stopped")

    visibility = permissions.evaluate_roster_agent_visibility(source, stopped_target)

    assert visibility.visible is True
    assert visibility.can_contact is False
    assert visibility.unavailable_reason == "agent_stopped"


def test_evaluate_roster_human_visibility_limits_private_to_creator_member():
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    source = make_agent(tenant_id=tenant_id, creator_id=creator_id, access_mode="private")
    creator_member = SimpleNamespace(tenant_id=tenant_id, user_id=creator_id, status="active")
    other_member = SimpleNamespace(tenant_id=tenant_id, user_id=uuid.uuid4(), status="active")

    assert permissions.evaluate_roster_human_visibility(source, creator_member).visible is True
    assert permissions.evaluate_roster_human_visibility(source, other_member).visible is False
