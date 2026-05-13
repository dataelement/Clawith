import uuid
from types import SimpleNamespace

from app.api.tools import _tool_record_visible_to_agent


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

