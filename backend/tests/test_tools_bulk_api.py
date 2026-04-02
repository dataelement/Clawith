import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.api import tools as tools_api
from app.core.security import get_current_user
from app.main import app


class FakeToolsResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class FakeDB:
    def __init__(self, tools):
        self._tools = tools
        self.committed = False

    async def execute(self, _statement):
        return FakeToolsResult(self._tools)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_put_tools_bulk_hits_bulk_route_and_updates_tools():
    tool_a = SimpleNamespace(id=uuid.uuid4(), enabled=False)
    tool_b = SimpleNamespace(id=uuid.uuid4(), enabled=True)
    db = FakeDB([tool_a, tool_b])

    async def override_db():
        yield db

    user = SimpleNamespace(
        id=uuid.uuid4(),
        role="platform_admin",
        tenant_id=uuid.uuid4(),
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[tools_api.get_db] = override_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.put(
            "/api/tools/bulk",
            json=[
                {"tool_id": str(tool_a.id), "enabled": True},
                {"tool_id": str(tool_b.id), "enabled": False},
            ],
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert tool_a.enabled is True
    assert tool_b.enabled is False
    assert db.committed is True
