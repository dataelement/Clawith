import pytest

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
