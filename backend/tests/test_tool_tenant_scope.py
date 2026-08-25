import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.tools import (
    _require_tool_manager,
    _require_tool_record_access,
    _resolve_target_tenant_id,
    _tool_record_visible_to_agent,
)


def make_tool(**overrides):
    values = {
        "id": uuid.uuid4(),
        "source": "builtin",
        "tenant_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_builtin_tools_are_visible_across_tenants():
    tenant_id = uuid.uuid4()
    tool = make_tool(source="builtin", tenant_id=None)

    assert _tool_record_visible_to_agent(tool, tenant_id, {}) is True


def test_admin_tools_are_visible_only_to_same_tenant():
    tenant_id = uuid.uuid4()
    foreign_tenant_id = uuid.uuid4()
    same_tenant_tool = make_tool(source="admin", tenant_id=tenant_id)
    foreign_tool = make_tool(source="admin", tenant_id=foreign_tenant_id)

    assert _tool_record_visible_to_agent(same_tenant_tool, tenant_id, {}) is True
    assert _tool_record_visible_to_agent(foreign_tool, tenant_id, {}) is False


def test_agent_installed_tools_require_explicit_assignment():
    tenant_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    installed_tool = make_tool(source="agent", id=tool_id, tenant_id=uuid.uuid4())

    assert _tool_record_visible_to_agent(installed_tool, tenant_id, {}) is False
    assert _tool_record_visible_to_agent(installed_tool, tenant_id, {str(tool_id): object()}) is True


def make_user(tenant_id: uuid.UUID, role: str = "user"):
    return SimpleNamespace(tenant_id=tenant_id, role=role)


def test_regular_users_cannot_access_tool_management():
    with pytest.raises(HTTPException, match="Tool management permission required") as error:
        _require_tool_manager(make_user(uuid.uuid4()))

    assert error.value.status_code == 403


def test_org_admin_cannot_select_another_tenant_for_tools():
    user = make_user(uuid.uuid4(), role="org_admin")

    with pytest.raises(HTTPException, match="No access to this tenant") as error:
        _resolve_target_tenant_id(user, str(uuid.uuid4()))

    assert error.value.status_code == 403


def test_platform_admin_can_select_another_tenant_for_tools():
    target_tenant_id = uuid.uuid4()
    user = make_user(uuid.uuid4(), role="platform_admin")

    assert _resolve_target_tenant_id(user, str(target_tenant_id)) == target_tenant_id


def test_org_admin_cannot_mutate_a_foreign_tenant_tool():
    user = make_user(uuid.uuid4(), role="org_admin")
    foreign_tool = make_tool(tenant_id=uuid.uuid4())

    with pytest.raises(HTTPException, match="No access to this tenant") as error:
        _require_tool_record_access(user, foreign_tool)

    assert error.value.status_code == 403
