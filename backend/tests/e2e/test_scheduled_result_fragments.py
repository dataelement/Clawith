"""No-destination outputs remain completely readable through authorized HTTP and Tools."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from e2e.test_attachments import login
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.capability_market.public import CatalogSpec
from app.modules.credential.public import Secret
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.tool.public import AgentToolResolutionScope, MCPTool
from app.modules.trigger.public import TriggerConfig, TriggerService


@pytest.mark.parametrize("kind", ["trigger", "heartbeat"])
@pytest.mark.parametrize("private", [False, True])
async def test_complete_scheduled_results_without_destination_are_authorized_and_paginated(
        test_database, composed_database, tmp_path, monkeypatch, kind, private):  # noqa: F811
    expected = "中🙂\\\n" * 5000
    occurrence_id = schedule_id = None
    tool_pages, denied, collected = [], [], {}
    def peer(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        if "This is unattended scheduled execution" in json.dumps(body["messages"]):
            return response({"content": expected})
        returned = [item for item in body["messages"] if item.get("tool_call_id", "").startswith("result-")]
        results = [json.loads(returned[-1]["content"])] if returned else []
        if results and "result" not in results[-1]:
            denied.append(results[-1])
            return response({"content": "Denied"})
        if results:
            collected[int(returned[-1]["tool_call_id"].removeprefix("result-"))] = results[-1]["result"]["content_json_fragment"]
        if results and results[-1]["result"]["next_offset"] is None:
            tool_pages.append("".join(collected[offset] for offset in sorted(collected)))
            return response({"content": "Read complete result"})
        if kind not in names:
            return call("search_tools", "find", {"query": kind})
        offset = results[-1]["result"]["next_offset"] if results else 0
        args = {"action": "result", "occurrence_id": str(occurrence_id), "content_offset": offset}
        if kind == "trigger":
            args["trigger_id"] = str(schedule_id)
        return call(kind, f"result-{offset}", args)
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        connections = ()
        if private:
            catalog = await app.state.execution.market.register(principal,
                spec=CatalogSpec("mcp", "http", "https://mcp.test/catalog", "Private source", "Private account", "1"))
            discovered = (MCPTool("mail", "Read mail", '{"type":"object"}'),)
            await app.state.execution.market.install_mcp(principal, agent_id=agent.id, item_id=catalog.item.id,
                endpoint="https://mcp.test/default", auth_required=False, discovered=discovered)
            async with transaction(test_database.sessions) as tx:
                tools = app.state.execution.tools(tx)
                captured = await tools.capture_authorized(AgentToolResolutionScope(principal.tenant_id, agent.id, "main"))
                definition = next(item.definition for item in captured.tools if item.definition.spec.source == "mcp")
                credential = await app.state.execution.credentials(tx).create(principal, kind="api_key", provider="mcp",
                    label="Private mailbox", secret=Secret("personal-test"), owner_kind="membership")
                connection = await tools.bind_personal_connection(principal, agent_id=agent.id, definition_id=definition.id,
                    credential_id=credential.id, label="Mail", endpoint="https://mcp.test/private", discovered=discovered)
                connections = (connection,)
        now = datetime.now(UTC)
        async with transaction(test_database.sessions) as tx:
            if kind == "trigger":
                config = await TriggerService(tx, enabled_sources=app.state.execution.market.enabled_source_ids).create(principal,
                    agent_id=agent.id, config=TriggerConfig("Result", "interval", "Produce complete result", interval_minutes=60),
                    delegated_connection_ids=connections, now=now)
            else:
                config = await HeartbeatService(tx, enabled_sources=app.state.execution.market.enabled_source_ids).configure(principal,
                    agent_id=agent.id, config=HeartbeatConfig("Produce complete result", 60), delegated_connection_ids=connections, now=now)
            schedule_id = config.id
        if kind == "trigger":
            occurrence = await app.state.scheduled.fire_manual(principal, trigger_id=schedule_id, event_id="result")
        else:
            async with transaction(test_database.sessions) as tx:
                owner = HeartbeatService(tx)
                due = (await owner.due(now=now + timedelta(minutes=61), not_before=now)).items[0]
                occurrence = await owner.accept(tenant_id=principal.tenant_id, heartbeat_id=schedule_id,
                    now=now + timedelta(minutes=61), not_before=now, source_key=due.source_key, due_at=due.due_at)
            await app.state.scheduled._execute(occurrence)
            async with transaction(test_database.sessions) as tx:
                occurrence = await HeartbeatService(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
        occurrence_id = occurrence.id
        assert occurrence.destination_kind is None
        await eventually(test_database.sessions, principal.tenant_id, occurrence.run_id, "Completed")
        headers = await login(client, app, principal, "owner")
        prefix = f"/api/triggers/{schedule_id}" if kind == "trigger" else f"/api/agents/{agent.id}/heartbeat"
        endpoint = f"{prefix}/history/{occurrence_id}/result"
        history = await client.get(prefix + "/history", headers=headers)
        assert history.status_code == 200 and history.json()["items"][0]["result"]["output_truncated"]
        pieces, offset = [], 0
        while True:
            result = await client.get(endpoint, headers=headers, params={"content_offset": offset})
            assert result.status_code == 200, result.text
            part = result.json()
            assert part["kind"] == "terminal_outcome" and len(part["content_json_fragment"]) <= 8000
            pieces.append(part["content_json_fragment"])
            if part["next_offset"] is None:
                break
            assert part["next_offset"] > offset
            offset = part["next_offset"]
        assert len(pieces) > 1 and json.loads("".join(pieces))["output"] == expected
        created = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
        started = await client.post(f"/api/sessions/{created.json()['id']}/inputs", headers=headers,
            json={"source_key": "inspect", "text": "Read the full scheduled result"})
        await eventually(test_database.sessions, principal.tenant_id, UUID(started.json()["run"]["run_id"]), "Completed")
        assert tool_pages and json.loads(tool_pages[0])["output"] == expected
        async with transaction(test_database.sessions) as tx:
            identities = IdentityService(tx)
            account = await identities.create_account()
            member = await identities.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
                display_name="Other administrator", role="tenant_admin")
            other = TenantPrincipal(account.id, member.id, principal.tenant_id, "tenant_admin")
        other_headers = await login(client, app, other, "other")
        viewed = await client.get(endpoint, headers=other_headers)
        assert viewed.status_code == (403 if private else 200), viewed.text
        if private:
            assert expected[:20] not in viewed.text
            created = await client.post("/api/sessions", headers=other_headers, json={"agent_id": str(agent.id)})
            started = await client.post(f"/api/sessions/{created.json()['id']}/inputs", headers=other_headers,
                json={"source_key": "inspect", "text": "Read the scheduled result"})
            await eventually(test_database.sessions, principal.tenant_id, UUID(started.json()["run"]["run_id"]), "Completed")
            assert denied and denied[-1]["code"] == "access_denied"
