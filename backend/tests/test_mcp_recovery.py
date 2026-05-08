import uuid

import pytest

from app.services import agent_tools as agent_tools_module
from app.services.mcp_client import MCPClient


@pytest.mark.asyncio
async def test_mcp_transport_error_keeps_streamable_failure_message(monkeypatch):
    client = MCPClient("https://example.test/mcp")

    async def fail_streamable(_method, _params=None):
        raise RuntimeError("streamable returned 401")

    async def fail_sse(_method, _params=None):
        raise RuntimeError("sse endpoint returned 404")

    monkeypatch.setattr(client, "_streamable_request", fail_streamable)
    monkeypatch.setattr(client, "_sse_request", fail_sse)

    with pytest.raises(Exception) as exc_info:
        await client._detect_and_request("tools/list")

    message = str(exc_info.value)
    assert "Streamable HTTP: streamable returned 401" in message
    assert "SSE: sse endpoint returned 404" in message


@pytest.mark.asyncio
async def test_smithery_recovery_does_not_store_auth_required_connection(monkeypatch):
    async def fake_ensure_connection(_api_key, _mcp_url, _display_name):
        return {
            "namespace": "shadowsseven",
            "connection_id": "new-auth-required",
            "auth_url": "https://smithery.run/shadowsseven/new-auth-required/setup",
        }

    def fail_if_db_touched():
        raise AssertionError("auth-required Smithery connections must not overwrite stored config")

    monkeypatch.setattr(
        "app.services.resource_discovery._ensure_smithery_connection",
        fake_ensure_connection,
    )
    monkeypatch.setattr(agent_tools_module, "async_session", fail_if_db_touched)

    result = await agent_tools_module._smithery_auto_recover(
        "smithery-key",
        "https://twitter.run.tools",
        "shadowsseven",
        "old-working-connection",
        agent_id=uuid.uuid4(),
    )

    assert "Re-authorization needed" in result
    assert "https://smithery.run/shadowsseven/new-auth-required/setup" in result
