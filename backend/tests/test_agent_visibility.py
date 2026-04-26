import uuid
from types import SimpleNamespace

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
    assert "agent_permissions.scope_type" in sql
