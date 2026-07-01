import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import directory as directory_api


def _make_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "OKR Assistant",
        "role_description": "Tracks OKR progress",
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "access_mode": "company",
        "status": "running",
        "is_expired": False,
        "expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.execute_count = 0

    async def execute(self, _statement, _params=None):
        self.execute_count += 1
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)


def test_agent_directory_router_uses_directory_prefix_only():
    assert directory_api.router.prefix == "/agents/{agent_id}/directory"
    assert "agent-directory" in directory_api.router.tags


def test_agent_directory_router_exposes_custom_maintenance_routes():
    paths = {route.path for route in directory_api.router.routes}

    assert "/custom/humans" in paths
    assert "/custom/human-candidates" in paths
    assert "/custom/humans/{user_id}" in paths
    assert "/custom/agents" in paths
    assert "/custom/agent-candidates" in paths
    assert "/custom/agents/{target_agent_id}" in paths


@pytest.mark.asyncio
async def test_get_agent_directory_filters_uncontactable_agents_by_default(monkeypatch):
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    running = _make_agent(tenant_id=tenant_id, name="Running Agent")
    stopped = _make_agent(tenant_id=tenant_id, name="Stopped Agent", status="stopped")
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[running, stopped]),
    ])

    async def fake_check_agent_access(_db, _current_user, _agent_id):
        return source, "use"

    monkeypatch.setattr(directory_api, "check_agent_access", fake_check_agent_access)

    result = await directory_api.get_agent_directory(
        agent_id=source.id,
        member_type="agent",
        current_user=SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id),
        db=db,
    )

    assert result["ok"] is True
    assert result["returned_count"] == 1
    assert result["members"][0]["target_agent_id"] == str(running.id)
    assert result["members"][0]["contact_tools"] == ["send_message_to_agent"]


@pytest.mark.asyncio
async def test_get_agent_directory_returns_structured_400_for_invalid_limit(monkeypatch):
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)

    async def fake_check_agent_access(_db, _current_user, _agent_id):
        return source, "manage"

    monkeypatch.setattr(directory_api, "check_agent_access", fake_check_agent_access)

    with pytest.raises(HTTPException) as exc:
        await directory_api.get_agent_directory(
            agent_id=source.id,
            limit=101,
            current_user=SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id),
            db=RecordingDB(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "invalid_limit"
