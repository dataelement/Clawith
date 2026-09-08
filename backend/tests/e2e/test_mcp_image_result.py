"""Real app/MCP/Run/Context/counting/Model path with only external HTTP replaced."""

import json
from uuid import uuid4

import httpx
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from sqlalchemy import text
from test_runtime_product_owner_fixture import ProductOwnerFixture, configure_agent, eventually

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.runtime import capture_snapshot
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.capability_market.public import CatalogSpec
from app.modules.run.public import InputContent, RunService, SourceIdentity, ToolResultPayload
from app.modules.tool.public import MCPClient, ToolResolutionScope, canonical_json
from app.modules.workspace.public import WorkspaceSubject

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9kAAAAASUVORK5CYII="
IMAGE_URL = "data:image/png;base64," + PNG


def image_urls(value):
    if isinstance(value, dict):
        if value.get("type") in ("image_url", "input_image"):
            candidate = value["image_url"]
            yield candidate["url"] if isinstance(candidate, dict) else candidate
        for child in value.values():
            yield from image_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from image_urls(child)


async def test_mcp_image_reaches_model_after_counting_and_original_result_remains_in_history(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811 — imported real DB pool fixture.
    generated, counted, mcp_calls = [], [], []
    raw_mcp_content = {"content": [{"type":"text", "text":"Chart supplied"},
        {"type":"image", "mimeType":"image/png", "data":PNG}], "structuredContent":{"caption":"A chart"}}

    def peer(request):
        if request.url.host == "mcp.invalid":
            assert "authorization" not in request.headers
            if request.method == "DELETE":
                return httpx.Response(204)
            body = json.loads(request.content)
            method = body["method"]
            mcp_calls.append(method)
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                result = {"protocolVersion":"2025-06-18", "capabilities":{"tools":{}}}
            elif method == "tools/list":
                result = {"tools":[{"name":"chart", "description":"Get a chart image", "inputSchema":{"type":"object"}}]}
            else:
                assert method == "tools/call" and body["params"]["name"] == "chart"
                result = raw_mcp_content
            return httpx.Response(200, headers={"Mcp-Session-Id":"mcp-image-session"},
                json={"jsonrpc":"2.0", "id":body["id"], "result":result})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if request.url.path.endswith("/responses/input_tokens"):
            counted.append(body)
            assert list(image_urls(body)) == [IMAGE_URL]
            return httpx.Response(200, json={"input_tokens":1000})
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return httpx.Response(200, json={"choices":[{"finish_reason":"tool_calls", "message":{
                "tool_calls":[{"id":"probe", "function":{"name":"capability_probe", "arguments":'{"value":"ok"}'}}]}}]})
        generated.append(body)
        completed_calls = {message.get("tool_call_id") for message in body["messages"] if message["role"] == "tool"}
        if "search-chart" not in completed_calls:
            assert not any(name.startswith("mcp_") for name in names)
            message, reason = {"tool_calls":[{"id":"search-chart", "function":{"name":"search_tools",
                "arguments":'{"query":"chart"}'}}]}, "tool_calls"
        elif "fetch-chart" not in completed_calls:
            selected = next(name for name in names if name.startswith("mcp_"))
            message, reason = {"tool_calls":[{"id":"fetch-chart", "function":{"name":selected, "arguments":'{}'}}]}, "tool_calls"
        else:
            assert counted
            assert list(image_urls(body)) == [IMAGE_URL]
            message, reason = {"content":"Chart reviewed."}, "stop"
        return httpx.Response(200, json={"choices":[{"message":message, "finish_reason":reason}]})

    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    owner = ProductOwnerFixture(test_database.schema)
    async with test_database.sessions.begin() as session:
        await session.execute(text(f"CREATE TABLE {owner.table} (run_id uuid PRIMARY KEY, status text, output text)"))
    app = application.create_app(configured(tmp_path), outcome_consumer=owner)
    async with app.router.lifespan_context(app):
        execution, runtime = app.state.execution, app.state.runtime
        principal, agent, model = await configure_agent(execution, test_database.sessions,
            capabilities={"supports_tool_calling":True, "supports_images":True, "image_token_counting":"openai_responses"})
        item = await execution.market.register(principal, spec=CatalogSpec("mcp", "http", "https://mcp.invalid/mcp",
            "Charts", "Chart image source", "1"))
        async with MCPClient(execution.http, endpoint="https://mcp.invalid/mcp", transport="streamable_http",
                token=None, auth_required=False) as client:
            discovered = await client.list_tools()
        installation = await execution.market.install_mcp(principal, agent_id=agent.id, item_id=item.item.id,
            endpoint="https://mcp.invalid/mcp", auth_required=False, discovered=discovered)
        assert installation.activated
        scope = await execution.workspace.direct_scope(principal, agent_id=agent.id, run_id=uuid4())
        await execution.workspace.ensure(scope, scope.output)
        await execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
        snapshot = await capture_snapshot(execution, app.state.database, agent=agent, model=model, workspace=scope,
            tools=ToolResolutionScope(principal, agent.id, "main"))
        started = await runtime.start(snapshot=snapshot, input=InputContent("Find the chart and explain it."),
            source=SourceIdentity("product_fixture", principal.membership_id, "mcp-image"))
        await eventually(test_database.sessions, principal.tenant_id, started.run.id, "Completed")
        async with transaction(test_database.sessions) as tx:
            history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=started.run.id)
            outcome = (await tx.session.execute(text(f"SELECT status, output FROM {owner.table}"))).one()
        image_result = next(entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload)
            and entry.payload.result.call_id == "fetch-chart")
        assert json.loads(image_result.content_json) == raw_mcp_content
        assert image_result.content_json == canonical_json(raw_mcp_content)
        assert tuple(outcome) == ("Completed", "Chart reviewed.")
        assert len(generated) == 3 and len(counted) >= 1
        assert mcp_calls.count("tools/call") == 1
        assert json.dumps(generated[-1]).count(PNG) == 1
        assert "A chart" in json.dumps(generated[-1])
        assert runtime.dispatcher.admitted == 0
    assert runtime.dispatcher.active == 0 and not hasattr(app.state, "runtime")
