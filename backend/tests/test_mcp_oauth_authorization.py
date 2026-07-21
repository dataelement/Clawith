from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from app.api import tools as tools_api
from app.services import resource_discovery


class _ConnectionResponse:
    status_code = 200

    def json(self):
        return {
            "status": {
                "state": "auth_required",
                "authorizationUrl": ("https://provider.example/authorize?api_key=url-secret"),
            }
        }


class _ConnectionClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        type(self).calls.append((url, kwargs))
        return _ConnectionResponse()


@pytest.mark.asyncio
async def test_smithery_connection_status_uses_one_read_and_preserves_url_in_memory_only(
    monkeypatch,
) -> None:
    monkeypatch.setattr(resource_discovery.httpx, "AsyncClient", _ConnectionClient)
    _ConnectionClient.calls = []

    status = await resource_discovery.get_smithery_connection_status(
        "server-secret",
        "tenant-namespace",
        "calendar-connection",
    )

    assert status == {
        "state": "auth_required",
        "authorization_url": "https://provider.example/authorize?api_key=url-secret",
    }
    assert len(_ConnectionClient.calls) == 1
    url, kwargs = _ConnectionClient.calls[0]
    assert url.endswith("/connect/tenant-namespace/calendar-connection")
    assert kwargs["headers"]["Authorization"] == "Bearer server-secret"


def test_import_auth_required_is_known_partial_with_secret_safe_receipt() -> None:
    outcome = resource_discovery._smithery_import_completion_outcome(
        display_name="Calendar",
        server_id="vendor/calendar",
        imported_tools=["Calendar: list", "Calendar: create"],
        connection={
            "namespace": "tenant-namespace",
            "connection_id": "calendar-connection",
            "state": "auth_required",
            "authorization_url": ("https://provider.example/authorize?api_key=url-secret"),
            "api_key": "server-secret",
        },
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_auth_required"
    assert outcome.retryable is False
    assert outcome.result_ref == ("smithery-connection:tenant-namespace:calendar-connection")
    assert "saved" in (outcome.result_summary or "").lower()
    assert "not available" in (outcome.result_summary or "").lower()
    serialized = json.dumps(
        {
            "summary": outcome.result_summary,
            "result_ref": outcome.result_ref,
            "metadata": outcome.metadata,
        }
    )
    assert "provider.example" not in serialized
    assert "url-secret" not in serialized
    assert "server-secret" not in serialized


@pytest.mark.asyncio
async def test_existing_import_rechecks_provider_instead_of_trusting_local_rows(
    monkeypatch,
) -> None:
    calls = 0

    async def status(_api_key, namespace, connection_id):
        nonlocal calls
        calls += 1
        assert namespace == "tenant-namespace"
        assert connection_id == "calendar-connection"
        return {
            "state": "auth_required",
            "authorization_url": "https://provider.example/authorize?token=secret",
        }

    monkeypatch.setattr(
        resource_discovery,
        "get_smithery_connection_status",
        status,
    )
    tools = [SimpleNamespace(display_name="Calendar: list")]
    assignments = [
        SimpleNamespace(
            config={
                "smithery_namespace": "tenant-namespace",
                "smithery_connection_id": "calendar-connection",
            }
        )
    ]

    outcome = await resource_discovery._existing_smithery_import_outcome(
        display_name="Calendar",
        server_id="vendor/calendar",
        existing_tools=tools,
        assignments=assignments,
        api_key="server-secret",
    )

    assert calls == 1
    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_auth_required"
    assert "provider.example" not in (outcome.result_summary or "")


@pytest.mark.asyncio
async def test_authorization_status_requires_manage_permission_and_never_calls_provider(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="member")
    agent = SimpleNamespace(id=agent_id, tenant_id=user.tenant_id)

    async def load_agent(_db, _agent_id):
        return agent

    async def deny_manage(_db, _user, _agent):
        return False

    async def forbidden_context(*_args, **_kwargs):
        raise AssertionError("assignment must not be read before manage permission")

    monkeypatch.setattr(tools_api, "_load_agent_for_tool_scope", load_agent)
    monkeypatch.setattr(tools_api, "can_manage_agent", deny_manage)
    monkeypatch.setattr(
        tools_api,
        "_load_assigned_smithery_connection",
        forbidden_context,
    )

    with pytest.raises(HTTPException) as exc_info:
        await tools_api.get_mcp_authorization_status(
            agent_id,
            tool_id,
            Response(),
            current_user=user,
            db=object(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.headers == {"Cache-Control": "no-store"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [
        (
            {"state": "connected"},
            {
                "provider": "smithery",
                "state": "connected",
                "connected": True,
            },
        ),
        (
            {
                "state": "auth_required",
                "authorization_url": "https://provider.example/authorize",
            },
            {
                "provider": "smithery",
                "state": "auth_required",
                "connected": False,
                "authorization_url": "https://provider.example/authorize",
            },
        ),
    ],
)
async def test_authorization_status_is_no_store_and_uses_server_side_coordinates(
    monkeypatch,
    provider_status,
    expected,
) -> None:
    agent_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="org_admin")
    agent = SimpleNamespace(id=agent_id, tenant_id=user.tenant_id)
    calls = []

    async def load_agent(_db, requested_agent_id):
        assert requested_agent_id == agent_id
        return agent

    async def allow_manage(_db, _user, _agent):
        return True

    async def load_connection(_db, requested_agent_id, requested_tool_id):
        assert requested_agent_id == agent_id
        assert requested_tool_id == tool_id
        return {
            "namespace": "server-namespace",
            "connection_id": "server-connection",
        }

    async def api_key(requested_agent_id):
        assert requested_agent_id == agent_id
        return "server-secret"

    async def status(key, namespace, connection_id):
        calls.append((key, namespace, connection_id))
        return provider_status

    monkeypatch.setattr(tools_api, "_load_agent_for_tool_scope", load_agent)
    monkeypatch.setattr(tools_api, "can_manage_agent", allow_manage)
    monkeypatch.setattr(
        tools_api,
        "_load_assigned_smithery_connection",
        load_connection,
    )
    monkeypatch.setattr(tools_api, "_get_smithery_api_key", api_key)
    monkeypatch.setattr(tools_api, "get_smithery_connection_status", status)

    response = Response()
    result = await tools_api.get_mcp_authorization_status(
        agent_id,
        tool_id,
        response,
        current_user=user,
        db=object(),
    )

    assert result == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [("server-secret", "server-namespace", "server-connection")]


@pytest.mark.asyncio
async def test_authorization_status_rejects_unassigned_or_non_smithery_tools(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="org_admin")

    async def load_agent(_db, _agent_id):
        return SimpleNamespace(id=agent_id, tenant_id=user.tenant_id)

    async def allow_manage(_db, _user, _agent):
        return True

    async def missing_connection(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tools_api, "_load_agent_for_tool_scope", load_agent)
    monkeypatch.setattr(tools_api, "can_manage_agent", allow_manage)
    monkeypatch.setattr(
        tools_api,
        "_load_assigned_smithery_connection",
        missing_connection,
    )

    with pytest.raises(HTTPException) as exc_info:
        await tools_api.get_mcp_authorization_status(
            agent_id,
            tool_id,
            Response(),
            current_user=user,
            db=object(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.headers == {"Cache-Control": "no-store"}


@pytest.mark.asyncio
async def test_authorization_status_unexpected_errors_are_generic_no_store_503(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="org_admin")
    agent = SimpleNamespace(id=agent_id, tenant_id=user.tenant_id)

    async def load_agent(_db, _agent_id):
        return agent

    async def allow_manage(_db, _user, _agent):
        return True

    async def load_connection(*_args, **_kwargs):
        return {
            "namespace": "server-namespace",
            "connection_id": "server-connection",
        }

    async def api_key(_agent_id):
        return "server-secret"

    async def broken_provider_status(*_args, **_kwargs):
        raise RuntimeError("provider.example/authorize?secret=must-not-leak")

    monkeypatch.setattr(tools_api, "_load_agent_for_tool_scope", load_agent)
    monkeypatch.setattr(tools_api, "can_manage_agent", allow_manage)
    monkeypatch.setattr(
        tools_api,
        "_load_assigned_smithery_connection",
        load_connection,
    )
    monkeypatch.setattr(tools_api, "_get_smithery_api_key", api_key)
    monkeypatch.setattr(
        tools_api,
        "get_smithery_connection_status",
        broken_provider_status,
    )

    response = Response()
    with pytest.raises(HTTPException) as exc_info:
        await tools_api.get_mcp_authorization_status(
            agent_id,
            tool_id,
            response,
            current_user=user,
            db=object(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "MCP authorization status unavailable"
    assert exc_info.value.headers == {"Cache-Control": "no-store"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "provider.example" not in str(exc_info.value)
