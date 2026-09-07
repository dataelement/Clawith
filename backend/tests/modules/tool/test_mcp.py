import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.http import create_stateless_http_client
from app.modules.credential.public import Secret
from app.modules.tool.execution import CallScope, ToolCall
from app.modules.tool.mcp import MCPClient, MCPExecutor, MCPFailure
from app.modules.tool.public import DefinitionSpec, ResolvedTool, ToolDefinition


def handler(request):
    if request.method == "DELETE":
        return httpx.Response(204)
    body = json.loads(request.content)
    if body["method"] == "notifications/initialized":
        return httpx.Response(202)
    result = (
        {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        if body["method"] == "initialize"
        else {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        if body["method"] == "tools/list"
        else {"content": [{"type": "text", "text": "done"}], "structuredContent": {"ok": True}}
    )
    return httpx.Response(
        200, headers={"Mcp-Session-Id": "test-session"}, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
    )


async def test_initialize_discovery_call_and_cleanup_without_credential():
    requests = []

    def observe(request):
        requests.append(request)
        assert "authorization" not in request.headers
        return handler(request)

    async with create_stateless_http_client(transport=httpx.MockTransport(observe)) as http:
        async with MCPClient(
            http, endpoint="https://mcp.test/mcp", transport="streamable_http", token=None, auth_required=False
        ) as client:
            assert (await client.list_tools())[0].name == "echo"
            assert (await client.call_tool(name="echo", arguments_json="{}"))["structuredContent"] == {"ok": True}
        assert requests[-1].method == "DELETE"
        assert requests[1].headers["Mcp-Session-Id"] == "test-session"
        assert requests[1].headers["MCP-Protocol-Version"] == "2025-06-18"


async def test_sse_matching_response_and_account_headers():
    def peer(request):
        assert request.headers["Authorization"] == "Bearer user-secret"
        if request.method == "DELETE":
            return handler(request)
        body = json.loads(request.content)
        response = handler(request)
        if body["method"] == "tools/list":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text='data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\ndata: ' + response.text + "\n\n",
            )
        return response

    async with (
        create_stateless_http_client(transport=httpx.MockTransport(peer)) as http,
        MCPClient(
            http,
            endpoint="https://mcp.test",
            transport="streamable_http",
            token=Secret("user-secret"),
            auth_required=True,
        ) as client,
    ):
        assert len(await client.list_tools()) == 1


async def test_discovery_cursor_loop_fails_bounded():
    def peer(request):
        response = handler(request)
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/list":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [], "nextCursor": "same"}}
            )
        return response

    async with (
        create_stateless_http_client(transport=httpx.MockTransport(peer)) as http,
        MCPClient(
            http, endpoint="https://mcp.test", transport="streamable_http", token=None, auth_required=False
        ) as client,
    ):
        with pytest.raises(MCPFailure, match="cursor"):
            await client.list_tools()


async def test_lost_business_reply_is_uncertain_and_never_replayed():
    calls = []

    def peer(request):
        if request.method == "POST":
            body = json.loads(request.content)
            calls.append(body["method"])
            if body["method"] == "tools/call":
                raise httpx.ReadError("secret remote detail", request=request)
        return handler(request)

    async def no_credential(*args):
        pytest.fail("uncredentialed MCP must not resolve Secret")

    tenant, agent = uuid4(), uuid4()
    tool = ResolvedTool(
        ToolDefinition(
            uuid4(), tenant, DefinitionSpec("echo", "echo", '{"type":"object"}', "mcp.v1", "mcp", uuid4(), "echo")
        ),
        None,
        "https://mcp.test",
    )
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await MCPExecutor(http, credentials=no_credential).execute(
            tool, ToolCall("call", "echo", "{}"), CallScope(tenant, agent, uuid4())
        )
    assert result.status == "uncertain"
    assert "secret" not in result.content_json
    assert calls.count("tools/call") == 1


def test_required_auth_is_not_fabricated():
    with pytest.raises(MCPFailure, match="authentication"):
        MCPClient(
            create_stateless_http_client(),
            endpoint="https://mcp.test",
            transport="streamable_http",
            token=None,
            auth_required=True,
        )


async def test_explicit_legacy_sse_transport_uses_same_stream_and_closes_it():
    queue = asyncio.Queue()
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: endpoint\ndata: /messages?sessionId=test\n\n"
            while True:
                yield await queue.get()

        async def aclose(self):
            closed.set()

    def peer(request):
        if request.method == "GET":
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())
        assert request.url.path == "/messages"
        body = json.loads(request.content)
        if "id" in body:
            response = handler(request)
            queue.put_nowait(("data: " + response.text + "\n\n").encode())
        return httpx.Response(202)

    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        async with MCPClient(
            http, endpoint="https://mcp.test/sse", transport="sse", token=None, auth_required=False
        ) as client:
            assert (await client.list_tools())[0].name == "echo"
            assert (await client.call_tool(name="echo", arguments_json="{}"))["content"]
        assert closed.is_set()


@pytest.mark.parametrize(
    "invalid",
    [
        {"type": "image", "data": "base64"},
        {"type": "unrecognized"},
        {"type": "resource", "resource": {"uri": "file:///missing"}},
    ],
)
async def test_invalid_content_fails_without_provider_body_leak(invalid):
    def peer(request):
        response = handler(request)
        if request.method == "POST":
            body = json.loads(request.content)
            if body["method"] == "tools/call":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [invalid]}})
        return response

    async with (
        create_stateless_http_client(transport=httpx.MockTransport(peer)) as http,
        MCPClient(
            http, endpoint="https://mcp.test", transport="streamable_http", token=None, auth_required=False
        ) as client,
    ):
        with pytest.raises(MCPFailure):
            await client.call_tool(name="echo", arguments_json="{}")


async def test_response_byte_limit_closes_response_stream():
    closed = asyncio.Event()

    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(129):
                yield b" " * 4096

        async def aclose(self):
            closed.set()

    def peer(request):
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/list":
            return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=Oversized())
        return handler(request)

    async with (
        create_stateless_http_client(transport=httpx.MockTransport(peer)) as http,
        MCPClient(
            http, endpoint="https://mcp.test", transport="streamable_http", token=None, auth_required=False
        ) as client,
    ):
        with pytest.raises(MCPFailure, match="byte limit"):
            await client.list_tools()
        assert closed.is_set()


async def test_shared_http_pool_never_reuses_other_accounts_cookies_or_default_auth():
    def peer(request):
        assert "cookie" not in request.headers
        assert request.headers.get("Authorization") == "Bearer selected-account"
        response = handler(request)
        response.headers["Set-Cookie"] = "account=previous; Path=/"
        return response

    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        http.cookies.set("account", "another")
        http.auth = httpx.BasicAuth("unrelated", "secret")
        http.headers["Authorization"] = "Bearer another"
        async with MCPClient(
            http,
            endpoint="https://mcp.test",
            transport="streamable_http",
            token=Secret("selected-account"),
            auth_required=True,
        ) as client:
            assert await client.list_tools()
            assert not list(http.cookies.jar)
        assert not list(http.cookies.jar)


async def test_mcp_rejects_stateful_client_at_construction_and_after_jar_replacement():
    async with httpx.AsyncClient() as ordinary:
        with pytest.raises(TypeError, match="stateless"):
            MCPClient(
                ordinary, endpoint="https://mcp.test", transport="streamable_http", token=None, auth_required=False
            )
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as http:
        client = MCPClient(
            http, endpoint="https://mcp.test", transport="streamable_http", token=None, auth_required=False
        )
        http.cookies = httpx.Cookies()
        with pytest.raises(TypeError, match="stateless"):
            async with client:
                pytest.fail("changed jar must fail before initialization")


def test_endpoint_allows_non_secret_parameters_but_not_embedded_credentials():
    from app.infrastructure.errors import InvalidInput
    from app.modules.tool.public import validate_endpoint

    assert validate_endpoint("https://mcp.test?region=cn") == "https://mcp.test?region=cn"
    for endpoint in ("https://mcp.test?api_key=secret", "https://user:secret@mcp.test", "https://[invalid"):
        with pytest.raises(InvalidInput):
            validate_endpoint(endpoint)
