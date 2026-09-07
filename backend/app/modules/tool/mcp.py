"""Bounded MCP HTTP adapters. No business call replay or account fallback."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal, Protocol, Self, cast
from urllib.parse import urljoin, urlsplit

import httpx

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
from app.modules.credential.public import Secret
from app.modules.tool.contracts import (
    MAX_DISCOVERY_BYTES,
    MAX_TOOLS,
    CredentialBinding,
    MCPTool,
    ResolvedTool,
    canonical_json,
    json_object,
    validate_endpoint,
)
from app.modules.tool.execution import CallScope, ToolCall, ToolResult

Transport = Literal["streamable_http", "sse"]
MAX_RESPONSE_BYTES = 524288
PROTOCOL_VERSION = "2025-06-18"


class MCPFailure(Exception):
    """Sanitized connection/protocol failure; never contains provider response bytes."""


class MCPClient:
    """One account and one HTTP session per context; injected HTTP client remains caller-owned."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        endpoint: str,
        transport: Transport,
        token: Secret | None,
        auth_required: bool,
        timeout_seconds: float = 30,
    ) -> None:
        self._http = http
        require_stateless_http_client(http)
        self._endpoint = validate_endpoint(endpoint)
        if transport not in ("streamable_http", "sse") or not 0 < timeout_seconds <= 120:
            raise InvalidInput("MCP transport configuration is invalid")
        if auth_required and token is None:
            raise MCPFailure("MCP authentication is required")
        self._transport = transport
        self._timeout = timeout_seconds
        self._headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token is not None:
            self._headers["Authorization"] = "Bearer " + token.value
        self._sequence = 0
        self._stack = AsyncExitStack()
        self._events: AsyncIterator[tuple[str, str]] | None = None
        self._post_endpoint = self._endpoint
        self._active = False

    async def __aenter__(self) -> Self:
        if self._active:
            raise MCPFailure("MCP client is already active")
        try:
            async with asyncio.timeout(self._timeout):
                if self._transport == "sse":
                    response = await self._stack.enter_async_context(
                        self._stream("GET", self._endpoint, headers=self._headers, timeout=self._timeout)
                    )
                    response.raise_for_status()
                    self._events = _sse_events(response)
                    event, data = await anext(self._events)
                    if event != "endpoint":
                        raise MCPFailure("MCP SSE endpoint event is missing")
                    target = urljoin(self._endpoint, data)
                    original, resolved = urlsplit(self._endpoint), urlsplit(target)
                    if (original.scheme, original.netloc) != (resolved.scheme, resolved.netloc):
                        raise MCPFailure("MCP SSE endpoint changed origin")
                    if resolved.username or resolved.password or resolved.fragment or len(target) > 4096:
                        raise MCPFailure("MCP SSE endpoint is invalid")
                    self._post_endpoint = target
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "clawith", "version": "1"},
                    },
                )
                version = result.get("protocolVersion")
                if version not in ("2024-11-05", "2025-03-26", PROTOCOL_VERSION):
                    raise MCPFailure("MCP negotiated an unsupported protocol version")
                if not isinstance(result.get("capabilities"), dict):
                    raise MCPFailure("MCP initialize capabilities are invalid")
                self._headers["MCP-Protocol-Version"] = str(version)
                await self._notification("notifications/initialized")
                self._active = True
                return self
        except (httpx.HTTPError, TimeoutError, StopAsyncIteration):
            await self.aclose()
            raise MCPFailure("MCP initialization failed") from None
        except BaseException:
            await self.aclose()
            raise

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._active = False
        try:
            if self._transport == "streamable_http" and "Mcp-Session-Id" in self._headers:
                try:
                    async with asyncio.timeout(2):
                        async with self._stream("DELETE", self._endpoint, headers=self._headers, timeout=2):
                            pass
                except (httpx.HTTPError, TimeoutError, MCPFailure):
                    # Remote session release is best-effort; never replay the completed call.
                    pass
        finally:
            self._headers.pop("Mcp-Session-Id", None)
            await self._stack.aclose()

    async def list_tools(self) -> tuple[MCPTool, ...]:
        self._require_active()
        tools: list[MCPTool] = []
        cursor: str | None = None
        seen: set[str] = set()
        total = 0
        async with asyncio.timeout(self._timeout):
            for _ in range(MAX_TOOLS + 1):
                result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
                total += len(canonical_json(result, maximum=MAX_RESPONSE_BYTES).encode())
                if total > MAX_DISCOVERY_BYTES:
                    raise MCPFailure("MCP discovery exceeds its byte limit")
                values = result.get("tools")
                if not isinstance(values, list) or len(values) + len(tools) > MAX_TOOLS:
                    raise MCPFailure("MCP discovery exceeds its Tool limit or has invalid shape")
                for value in values:
                    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                        raise MCPFailure("MCP Tool metadata is invalid")
                    description = value.get("description", "")
                    if not isinstance(description, str):
                        raise MCPFailure("MCP Tool description is invalid")
                    tools.append(MCPTool(value["name"], description, canonical_json(value.get("inputSchema"))))
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    if len({tool.name for tool in tools}) != len(tools):
                        raise MCPFailure("MCP discovery contains duplicate Tool identities")
                    return tuple(tools)
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or len(next_cursor) > 4096
                    or next_cursor in seen
                ):
                    raise MCPFailure("MCP discovery cursor is invalid")
                seen.add(next_cursor)
                cursor = next_cursor
        raise MCPFailure("MCP discovery page limit exceeded")

    async def call_tool(self, *, name: str, arguments_json: str) -> dict[str, object]:
        self._require_active()
        if not name or len(name) > 256:
            raise InvalidInput("MCP upstream Tool name is invalid")
        async with asyncio.timeout(self._timeout):
            result = await self._request("tools/call", {"name": name, "arguments": json_object(arguments_json)})
        content = result.get("content")
        if not isinstance(content, list) or len(content) > 128:
            raise MCPFailure("MCP Tool content is invalid")
        for item in content:
            _validate_content(item)
        if "isError" in result and not isinstance(result["isError"], bool):
            raise MCPFailure("MCP Tool error flag is invalid")
        if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
            raise MCPFailure("MCP structured content is invalid")
        return result

    def _require_active(self) -> None:
        if not self._active:
            raise MCPFailure("MCP client is not initialized")

    @asynccontextmanager
    async def _stream(
        self, method: str, url: str, *, headers: dict[str, str], timeout: float, json: dict[str, object] | None = None
    ) -> AsyncIterator[httpx.Response]:
        # Share the transport pool, never its mutable cookie jar or default account auth.
        request = httpx.Request(
            method, url, headers=headers, json=json, extensions={"timeout": httpx.Timeout(timeout).as_dict()}
        )
        require_stateless_http_client(self._http)
        try:
            response = await self._http.send(request, stream=True, follow_redirects=False, auth=None)
        except httpx.HTTPError:
            raise MCPFailure("MCP transport failed") from None
        try:
            yield response
        except httpx.HTTPError:
            raise MCPFailure("MCP transport failed") from None
        finally:
            await response.aclose()

    async def _notification(self, method: str) -> None:
        async with self._stream(
            "POST",
            self._post_endpoint,
            json={"jsonrpc": "2.0", "method": method},
            headers=self._headers,
            timeout=self._timeout,
        ) as response:
            if response.status_code != 202:
                raise MCPFailure("MCP notification was rejected")

    async def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self._sequence += 1
        request_id = self._sequence
        async with self._stream(
            "POST",
            self._post_endpoint,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers=self._headers,
            timeout=self._timeout,
        ) as response:
            response.raise_for_status()
            if method == "initialize" and "mcp-session-id" in response.headers:
                session_id = response.headers["mcp-session-id"]
                if not session_id or len(session_id) > 512 or any(not 33 <= ord(c) <= 126 for c in session_id):
                    raise MCPFailure("MCP session identity is invalid")
                self._headers["Mcp-Session-Id"] = session_id
            if self._transport == "sse":
                if response.status_code != 202 or self._events is None:
                    raise MCPFailure("MCP SSE request was rejected")
                return await _read_matching(self._events, request_id)
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if content_type == "text/event-stream":
                return await _read_matching(_sse_events(response), request_id)
            if content_type != "application/json":
                raise MCPFailure("MCP response content type is invalid")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise MCPFailure("MCP response exceeds its byte limit")
                data.extend(chunk)
            try:
                payload = json_object(bytes(data).decode("utf-8"), maximum=MAX_RESPONSE_BYTES)
            except (InvalidInput, UnicodeError):
                raise MCPFailure("MCP response JSON is invalid") from None
            return _result(payload, request_id)


async def _sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    # Byte bounds apply before text/line materialization, including SSE framing.
    buffer = bytearray()
    total = 0
    event = "message"
    data: list[str] = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise MCPFailure("MCP event stream exceeds its byte limit")
        buffer.extend(chunk)
        while b"\n" in buffer:
            line_bytes, _, remaining = buffer.partition(b"\n")
            buffer = bytearray(remaining)
            try:
                line = bytes(line_bytes).rstrip(b"\r").decode("utf-8")
            except UnicodeError:
                raise MCPFailure("MCP event stream is not UTF-8") from None
            if not line:
                if data:
                    yield event, "\n".join(data)
                event, data = "message", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
    if data or buffer:
        raise MCPFailure("MCP event stream ended with an incomplete event")


async def _read_matching(events: AsyncIterator[tuple[str, str]], request_id: int) -> dict[str, object]:
    async for _, data in events:
        payload = json_object(data, maximum=MAX_RESPONSE_BYTES)
        if payload.get("id") == request_id and "method" not in payload:
            return _result(payload, request_id)
        if "id" in payload:
            raise MCPFailure("MCP returned an unsupported request or mismatched response")
        if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            raise MCPFailure("MCP notification is invalid")
    raise MCPFailure("MCP stream ended without the requested result")


def _result(payload: dict[str, object], request_id: int) -> dict[str, object]:
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
        raise MCPFailure("MCP response identity is invalid")
    if "error" in payload:
        raise MCPFailure("MCP request returned a protocol error")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MCPFailure("MCP result is invalid")
    return cast(dict[str, object], result)


def _validate_content(item: object) -> None:
    if not isinstance(item, dict):
        raise MCPFailure("MCP content block is invalid")
    kind = item.get("type")
    required: tuple[str, ...]
    if kind == "text":
        required = ("text",)
    elif kind in ("image", "audio"):
        required = ("data", "mimeType")
    elif kind == "resource_link":
        required = ("uri", "name")
    elif kind == "resource":
        resource = item.get("resource")
        if (
            not isinstance(resource, dict)
            or not isinstance(resource.get("uri"), str)
            or not any(isinstance(resource.get(key), str) for key in ("text", "blob"))
        ):
            raise MCPFailure("MCP embedded resource is invalid")
        required = ()
    else:
        raise MCPFailure("MCP content type is unsupported")
    if any(not isinstance(item.get(field), str) for field in required):
        raise MCPFailure("MCP content block fields are invalid")


class CredentialResolver(Protocol):
    async def __call__(self, binding: CredentialBinding, scope: CallScope) -> Secret: ...


class MCPExecutor:
    """Secret resolution finishes before network work; composition owns the resolver transaction."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        credentials: CredentialResolver,
        client_factory: Callable[..., MCPClient] = MCPClient,
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._client_factory = client_factory

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        if tool.definition.tenant_id != scope.tenant_id or (
            tool.credential and tool.credential.owner_kind == "agent" and tool.credential.owner_id != scope.agent_id
        ):
            raise AccessDenied("MCP invocation is outside the resolved scope")
        if tool.endpoint is None or tool.definition.spec.upstream_name is None:
            raise InvalidInput("MCP execution binding is incomplete")
        token = await self._credentials(tool.credential, scope) if tool.credential else None
        dispatched = False
        try:
            async with self._client_factory(
                self._http,
                endpoint=tool.endpoint,
                transport=tool.transport,
                token=token,
                auth_required=tool.credential is not None,
            ) as client:
                dispatched = True
                result = await client.call_tool(
                    name=tool.definition.spec.upstream_name, arguments_json=call.arguments_json
                )
                content: dict[str, object] = {"content": result["content"]}
                if "structuredContent" in result:
                    content["structuredContent"] = result["structuredContent"]
                encoded = canonical_json(content, maximum=262000)
                return ToolResult(call.id, "error" if result.get("isError") else "success", encoded)
        except (httpx.HTTPError, TimeoutError, MCPFailure, InvalidInput, StopAsyncIteration):
            message = (
                "MCP result was not confirmed; verify the external effect before repeating."
                if dispatched
                else "MCP connection failed; check its configuration and selected account."
            )
            return ToolResult(call.id, "uncertain" if dispatched else "error", canonical_json({"message": message}))
