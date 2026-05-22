import uuid
from types import SimpleNamespace

import pytest

from app.api import tools as tools_api
from app.api.tools import _tool_record_visible_to_agent
from app.services import tool_seeder


class DummyResult:
    def __init__(self, rows=None, *, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.statements = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(str(statement))
        return self.responses.pop(0) if self.responses else DummyResult()

    async def commit(self):
        self.committed = True


class FakeSessionFactory:
    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


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


@pytest.mark.asyncio
async def test_list_tools_query_keeps_builtin_tools_when_tenant_is_selected():
    tenant_id = uuid.uuid4()
    current_user = SimpleNamespace(role="org_admin", tenant_id=tenant_id, identity=None)
    db = RecordingDB([DummyResult([])])

    await tools_api.list_tools(tenant_id=str(tenant_id), current_user=current_user, db=db)

    sql = db.statements[0]
    assert "tools.tenant_id IS NULL" in sql
    assert "tools.tenant_id =" in sql


@pytest.mark.asyncio
async def test_clean_orphaned_mcp_tools_only_targets_global_agent_records(monkeypatch):
    db = RecordingDB([DummyResult([]), DummyResult(rowcount=0)])
    monkeypatch.setattr(tool_seeder, "async_session", FakeSessionFactory(db))

    await tool_seeder.clean_orphaned_mcp_tools()

    sql = db.statements[1]
    assert "tools.type =" in sql
    assert "tools.source =" in sql
    assert "tools.tenant_id IS NULL" in sql
    assert db.committed is True
