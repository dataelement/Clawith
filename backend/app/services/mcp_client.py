"""MCP (Model Context Protocol) Client — connects to external MCP servers.

Supports two transport modes:
1. Streamable HTTP (modern) — single URL, POST JSON-RPC, response as JSON or SSE
2. SSE Transport (legacy but widely used) — GET /sse for event stream, POST /messages for requests

Transport is auto-detected: tries Streamable HTTP first, falls back to SSE.
Reference: https://modelcontextprotocol.io/docs
"""

import httpx
import json
import asyncio
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from loguru import logger


class MCPClient:
    """Client for connecting to MCP servers via Streamable HTTP or SSE transport.

    Auto-detects the transport mode on first request.
    """

    def __init__(self, server_url: str, api_key: str | None = None):
        # Extract apiKey from URL query params and move to Authorization header
        parsed = urlparse(server_url)
        qs = parse_qs(parsed.query, keep_blank_values=True)

        self.api_key = api_key
        if not self.api_key and "apiKey" in qs:
            self.api_key = qs.pop("apiKey")[0]

        # Rebuild URL without apiKey in query string
        remaining_qs = urlencode({k: v[0] for k, v in qs.items()}) if qs else ""
        self.server_url = urlunparse(parsed._replace(query=remaining_qs)).rstrip("/")

        # Transport state
        self._transport: str | None = None  # "streamable" or "sse"
        self._session_id: str | None = None
        self._sse_messages_url: str | None = None  # POST endpoint for SSE transport

    def _headers(self) -> dict:
        """Build request headers with proper MCP and auth headers."""
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _parse_response(self, resp: httpx.Response) -> dict:
        """Parse response — handles both JSON and SSE (text/event-stream) formats."""
        content_type = resp.headers.get("content-type", "")

        # Save session ID if the server returns one
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if "text/event-stream" in content_type:
            return self._parse_sse_response(resp.text)
        else:
            return resp.json()

    def _parse_sse_response(self, text: str) -> dict:
        """Extract the last JSON-RPC result from an SSE stream."""
        last_data = None
        for line in text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "[DONE]":
                    try:
                        last_data = json.loads(raw)
                    except json.JSONDecodeError:
                        pass
        if last_data is None:
            raise Exception("No valid JSON found in SSE response")
        return last_data

    # ── Streamable HTTP Transport ────────────────────────────────

    async def _streamable_initialize(self, client: httpx.AsyncClient) -> None:
        """Send MCP initialize + initialized handshake (Streamable HTTP)."""
        try:
            resp = await client.post(
                self.server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "clawith", "version": "1.0"},
                    },
                },
                headers=self._headers(),
            )
            if resp.status_code == 200:
                self._parse_response(resp)  # captures Mcp-Session-Id if present
            # Send initialized notification (required by MCP spec before other requests)
            await client.post(
                self.server_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._headers(),
            )
        except Exception:
            pass  # initialization failure is non-fatal — server may be stateless

    async def _streamable_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request via Streamable HTTP transport."""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            if not self._session_id:
                await self._streamable_initialize(client)

            body: dict = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}

            resp = await client.post(self.server_url, json=body, headers=self._headers())
            if resp.status_code not in (200, 201):
                raise Exception(f"HTTP {resp.status_code}")
            return self._parse_response(resp)

    # ── SSE Transport ────────────────────────────────────────────

    async def _sse_connect(self) -> str:
        """Connect to SSE endpoint (GET /sse) and extract the messages URL.

        Returns the full POST URL for sending JSON-RPC messages.
        """
        # Determine SSE URL: if server_url ends with /sse use it directly,
        # otherwise append /sse
        sse_url = self.server_url if self.server_url.endswith("/sse") else f"{self.server_url}/sse"
        parsed = urlparse(sse_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        headers = {"Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages_url = None

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            async with client.stream("GET", sse_url, headers=headers) as resp:
                if resp.status_code != 200:
                    raise Exception(f"SSE connect failed: HTTP {resp.status_code}")

                # Read SSE events until we get the endpoint event
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event_type == "endpoint" and data:
                            # data is typically a relative URL like /messages?sessionId=xxx
                            if data.startswith("http"):
                                messages_url = data
                            else:
                                messages_url = base_url + data
                            break
                    elif line == "":
                        # Empty line = end of SSE event block
                        pass

        if not messages_url:
            raise Exception("SSE endpoint did not return a messages URL")

        return messages_url

    async def _sse_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request via SSE transport.

        Opens a fresh SSE connection each call to get the messages endpoint,
        sends the JSON-RPC request, then reads responses from the SSE stream.
        """
        # Connect to SSE to get the messages endpoint
        sse_url = self.server_url if self.server_url.endswith("/sse") else f"{self.server_url}/sse"
        parsed = urlparse(sse_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        headers_sse = {"Accept": "text/event-stream"}
        headers_post = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.api_key:
            headers_sse["Authorization"] = f"Bearer {self.api_key}"
            headers_post["Authorization"] = f"Bearer {self.api_key}"

        body: dict = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}

        timeout = 60 if method == "tools/call" else 30

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # Open the SSE stream
            async with client.stream("GET", sse_url, headers=headers_sse) as sse_resp:
                if sse_resp.status_code != 200:
                    raise Exception(f"SSE connect failed: HTTP {sse_resp.status_code}")

                messages_url = None
                event_type = ""

                # Phase 1: Read until we get the endpoint event
                line_iter = sse_resp.aiter_lines()
                async for line in line_iter:
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event_type == "endpoint" and data:
                            if data.startswith("http"):
                                messages_url = data
                            else:
                                messages_url = base_url + data
                            break

                if not messages_url:
                    raise Exception("SSE endpoint did not return a messages URL")

                # Phase 2: MCP handshake — initialize + initialized notification
                init_body = {
                    "jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "clawith", "version": "1.0"},
                    },
                }
                await client.post(messages_url, json=init_body, headers=headers_post)
                # Send initialized notification (required before other requests)
                await client.post(
                    messages_url,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers=headers_post,
                )

                # Send the actual request
                post_resp = await client.post(messages_url, json=body, headers=headers_post)

                # Phase 3: Read the response — either from POST response or from SSE stream
                if post_resp.status_code == 200:
                    ct = post_resp.headers.get("content-type", "")
                    if "application/json" in ct:
                        return post_resp.json()

                # Read response from SSE stream
                result = None
                async for line in line_iter:
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event_type == "message" and data:
                            try:
                                parsed_data = json.loads(data)
                                # Match our request ID
                                if isinstance(parsed_data, dict) and parsed_data.get("id") in (0, 1):
                                    result = parsed_data
                                    if parsed_data.get("id") == 1:
                                        break  # Got our actual request response
                            except json.JSONDecodeError:
                                pass

                if result is None:
                    raise Exception("No response received from SSE transport")
                return result

    # ── Auto-detect Transport ────────────────────────────────────

    async def _detect_and_request(self, method: str, params: dict | None = None) -> dict:
        """Auto-detect transport and send request.

        Strategy: If transport is already known, use it directly.
        Otherwise try Streamable HTTP first, fall back to SSE.
        """
        if self._transport == "sse":
            return await self._sse_request(method, params)
        if self._transport == "streamable":
            return await self._streamable_request(method, params)

        # Auto-detect: try Streamable HTTP first. Python clears exception
        # variables after an `except ... as name` block exits, so keep a stable
        # string copy for the later SSE fallback error.
        streamable_error_message = ""
        try:
            result = await self._streamable_request(method, params)
            self._transport = "streamable"
            return result
        except Exception as streamable_err:
            streamable_error_message = str(streamable_err)
            logger.info(f"[MCPClient] Streamable HTTP failed ({streamable_err}), trying SSE transport...")

        # Fallback to SSE
        try:
            result = await self._sse_request(method, params)
            self._transport = "sse"
            return result
        except Exception as sse_err:
            raise Exception(
                f"Both transports failed. "
                f"Streamable HTTP: {streamable_error_message}; "
                f"SSE: {sse_err}"
            )

    # ── Public API ───────────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """Fetch available tools from the MCP server."""
        try:
            data = await self._detect_and_request("tools/list")

            if "error" in data:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise Exception(f"MCP error: {msg}")

            result = data.get("result", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            return [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                }
                for t in tools
            ]
        except httpx.HTTPError as e:
            raise Exception(f"Connection failed: {str(e)[:200]}")

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on the MCP server."""
        try:
            data = await self._detect_and_request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )

            if "error" in data:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return f"❌ MCP tool execution error: {msg[:200]}"

            result = data.get("result", {})
            if isinstance(result, str):
                return result

            # MCP returns content as list of content blocks
            content_blocks = result.get("content", []) if isinstance(result, dict) else []
            texts = []
            for block in content_blocks:
                if isinstance(block, str):
                    texts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        texts.append(f"[Image: {block.get('mimeType', 'image')}]")
                    else:
                        texts.append(str(block))
                else:
                    texts.append(str(block))

            return "\n".join(texts) if texts else str(result)

        except httpx.HTTPError as e:
            return f"❌ MCP connection failed: {str(e)[:200]}"
