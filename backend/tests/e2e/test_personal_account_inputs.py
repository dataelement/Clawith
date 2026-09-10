"""Real personal Credential selection reaches MCP HTTP without transitive delegation."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.errors import Conflict
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService
from app.modules.agent.public import AgentService
from app.modules.capability_market.public import CatalogSpec
from app.modules.credential.public import Secret
from app.modules.group.public import GroupService
from app.modules.heartbeat.public import HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, RunService, ToolResultPayload
from app.modules.session.public import SessionService
from app.modules.tool.public import AgentToolResolutionScope, MCPTool, PersonalAccountSelection
from app.modules.trigger.public import TriggerService


async def prepare_accounts(app, sessions):
    principal, source, _ = await configure_agent(app.state.execution, sessions)
    agents = {"source": source}
    async with transaction(sessions) as tx:
        await AgentService(tx).update(principal, agent_id=source.id, soul="ACTOR_SOURCE")
        for label in ("middle", "final"):
            agent = await AgentService(tx).create(principal, name=label, soul="ACTOR_" + label.upper(),
                timezone="UTC", model_id=source.model_id)
            await provision_builtin_tools(tx, principal, agent_id=agent.id)
            await PermissionService(tx).set_visibility(principal, agent_id=agent.id, visibility="tenant")
            agents[label] = agent
    catalog = await app.state.execution.market.register(principal, spec=CatalogSpec("mcp", "http",
        "https://mcp.invalid/catalog", "Mail", "Mailbox access", "1"))
    discovery = (MCPTool("mail", "Read current mailbox", '{"type":"object"}'),)
    personal = {}
    for label, agent in agents.items():
        installed = await app.state.execution.market.install_mcp(principal, agent_id=agent.id, item_id=catalog.item.id,
            endpoint=f"https://mcp.invalid/{label}/default", auth_required=False, discovered=discovery)
        assert installed.activated
        async with transaction(sessions) as tx:
            tools = app.state.execution.tools(tx)
            captured = await tools.capture_authorized(AgentToolResolutionScope(principal.tenant_id, agent.id, "main"))
            definition = next(item.definition for item in captured.tools if item.definition.spec.source == "mcp")
            credential = await app.state.execution.credentials(tx).create(principal, kind="api_key", provider="mcp",
                label=f"User {label}", secret=Secret(f"personal-{label}"), owner_kind="membership")
            personal[label] = await tools.bind_personal_connection(principal, agent_id=agent.id, definition_id=definition.id,
                credential_id=credential.id, label="User mailbox", endpoint=f"https://mcp.invalid/{label}/personal", discovered=discovery)
    return principal, agents, personal


def model_and_mcp(agents, calls, observed):
    def peer(request):
        if request.url.host == "mcp.invalid":
            if request.method == "DELETE":
                return httpx.Response(204)
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            if body["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            else:
                assert body["method"] == "tools/call"
                calls.append((request.url.path, request.headers.get("authorization")))
                result = {"content": [{"type": "text", "text": "Mailbox observed"}]}
            return httpx.Response(200, headers={"Mcp-Session-Id": "account-test"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        encoded = json.dumps(body["messages"])
        actor = next(label for label in ("source", "middle", "final") if "ACTOR_" + label.upper() in encoded)
        completed = {message.get("tool_call_id") for message in body["messages"] if message["role"] == "tool"}
        mcp = next((name for name in names if name.startswith("mcp_")), None)
        if mcp is None:
            return call("search_tools", "find-mail", {"query": "mailbox"})
        if "mail-read" not in completed:
            return call(mcp, "mail-read", {})
        if actor == "middle":
            if "save-heartbeat" not in completed:
                if "heartbeat" not in names:
                    return call("search_tools", "find-heartbeat", {"query": "heartbeat"})
                return call("heartbeat", "save-heartbeat", {"action": "configure", "enabled": False,
                    "config": {"instruction": "Future work", "interval_minutes": 10}})
            if "save-trigger" not in completed:
                if "trigger" not in names:
                    return call("search_tools", "find-trigger", {"query": "trigger"})
                return call("trigger", "save-trigger", {"action": "create", "enabled": False,
                    "config": {"name": "future", "kind": "interval", "instruction": "Future work", "interval_minutes": 10}})
        if actor != "final" and "forward" not in completed:
            if "send_message_to_agent" not in names:
                return call("search_tools", "find-a2a", {"query": "send_message_to_agent"})
            target = agents["middle" if actor == "source" else "final"]
            return call("send_message_to_agent", "forward", {"action": "send", "target_agent_id": str(target.id),
                "intent": "notify", "text": "Use your available mailbox and finish this work."})
        return response({"content": "Work completed"})
    return peer


async def forwarded_run(sessions, tenant_id, source_run_id):
    async with asyncio.timeout(15):
        while True:
            async with transaction(sessions) as tx:
                history = await RunService(tx).read_history(tenant_id=tenant_id, run_id=source_run_id)
                output = next((entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload)
                    and entry.payload.result.call_id == "forward"), None)
                if output is not None:
                    assert output.status == "success", output.content_json
                    body = json.loads(output.content_json)
                    assert body["accepted"], body
                    request = await A2AService(tx).get(tenant_id=tenant_id, request_id=UUID(body["request_id"]))
                    if request.target_run_id is not None:
                        return request
            await asyncio.sleep(.02)


@pytest.mark.parametrize("entry_kind", ["session", "group"])
async def test_personal_http_credentials_are_exact_and_not_forwarded_again(
        test_database, composed_database, tmp_path, monkeypatch, entry_kind):  # noqa: F811
    agents, calls, observed = {}, [], []
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(model_and_mcp(agents, calls, observed)), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, configured_agents, personal = await prepare_accounts(app, test_database.sessions)
        agents.update(configured_agents)
        selections = tuple(PersonalAccountSelection(agents[label].id, (personal[label],)) for label in agents)
        if entry_kind == "session":
            await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="account-person", password="password")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                login = await client.post("/api/auth/login", json={"login_name": "account-person", "password": "password", "tenant_id": str(principal.tenant_id)})
                headers = {"Authorization": "Bearer " + login.json()["token"]}
                created = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agents["source"].id)})
                session_id = created.json()["id"]
                accepted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={
                    "source_key": "accounts", "text": "Read mail, then delegate explicitly.",
                    "account_selections": [{"target_agent_id": str(item.target_agent_id), "connection_ids": [str(id) for id in item.connection_ids]} for item in selections]})
                assert accepted.status_code == 202 and accepted.json()["error"] is None, accepted.text
                source_run = UUID(accepted.json()["run"]["run_id"])
        else:
            async with transaction(test_database.sessions) as tx:
                group = await GroupService(tx).create(principal, name="Mail group")
                await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=agents["source"].id, enabled=True)
            accepted = await app.state.products.other.submit_group(principal, group_id=group.id, source_key="accounts",
                input=InputContent("Read mail, then delegate explicitly."), agent_ids=(agents["source"].id,), account_selections=selections)
            assert not accepted.errors
            source_run = accepted.runs[0].id
        middle = await forwarded_run(test_database.sessions, principal.tenant_id, source_run)
        final = await forwarded_run(test_database.sessions, principal.tenant_id, middle.target_run_id)
        for id in (source_run, middle.target_run_id, final.target_run_id):
            await eventually(test_database.sessions, principal.tenant_id, id, "Completed")
        assert middle.delegated_connection_ids == (personal["middle"],)
        assert final.delegated_connection_ids == ()
        assert set(calls) == {("/source/personal", "Bearer personal-source"), ("/middle/personal", "Bearer personal-middle"), ("/final/default", None)}
        assert len(calls) == 3
        async with transaction(test_database.sessions) as tx:
            heartbeat = await HeartbeatService(tx).get(principal, agent_id=agents["middle"].id)
            triggers = await TriggerService(tx).list(principal, agent_id=agents["middle"].id)
            assert not heartbeat.delegated_connection_ids
            assert len(triggers) == 1 and not triggers[0].delegated_connection_ids
        model_text = json.dumps(observed)
        assert all(str(id) not in model_text for id in personal.values())
        assert "personal-source" not in model_text and "personal-middle" not in model_text
        if entry_kind == "session":
            async with transaction(test_database.sessions) as tx:
                owner = SessionService(tx, enabled_sources=app.state.execution.market.enabled_source_ids)
                run = await RunService(tx).get(tenant_id=principal.tenant_id, run_id=source_run)
                fragment = await owner.read_execution_history_fragment(run)
                assert "account_selections" not in fragment.content_json
                assert all(str(id) not in fragment.content_json for id in personal.values())
                goal_session = await owner.create(principal, agent_id=agents["source"].id)
                goal_input = await owner.accept_input(principal, session_id=goal_session.id, source_key="goal-accounts",
                    input=InputContent("Continue mailbox work"), account_selections=selections)
                goal = await owner.enable_goal(principal, session_id=goal_session.id, input_id=goal_input.entry.id, objective="Mailbox goal")
                assert await owner.goal_accounts(tenant_id=principal.tenant_id, session_id=goal_session.id,
                    expected_link_id=goal.current_link_id) == (personal["source"],)
                with pytest.raises(Conflict):
                    await owner.goal_accounts(tenant_id=principal.tenant_id, session_id=goal_session.id, expected_link_id=uuid4())


@pytest.mark.parametrize("wrong_owner", [False, True])
async def test_http_rejects_wrong_agent_or_membership_account_before_any_execution(
        test_database, composed_database, tmp_path, monkeypatch, wrong_owner):  # noqa: F811
    agents, calls, observed = {}, [], []
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(model_and_mcp(agents, calls, observed)), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, configured_agents, personal = await prepare_accounts(app, test_database.sessions)
        agents.update(configured_agents)
        selected = personal["source"]
        if wrong_owner:
            async with transaction(test_database.sessions) as tx:
                identities = IdentityService(tx)
                account = await identities.create_account()
                member = await identities.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
                    display_name="Other person", role="member")
                other = TenantPrincipal(account.id, member.id, principal.tenant_id, "member", allowed_agent_ids=frozenset({agents["middle"].id}))
                tools = app.state.execution.tools(tx)
                captured = await tools.capture_authorized(AgentToolResolutionScope(principal.tenant_id, agents["middle"].id, "main"))
                definition = next(item.definition for item in captured.tools if item.definition.spec.source == "mcp")
                credential = await app.state.execution.credentials(tx).create(other, kind="api_key", provider="mcp",
                    label="Another person's mailbox", secret=Secret("not-authorized"), owner_kind="membership")
                selected = await tools.bind_personal_connection(other, agent_id=agents["middle"].id, definition_id=definition.id,
                    credential_id=credential.id, label="Other account", endpoint="https://mcp.invalid/middle/other",
                    discovered=(MCPTool("mail", "Read current mailbox", '{"type":"object"}'),))
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="denied-person", password="password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"login_name": "denied-person", "password": "password", "tenant_id": str(principal.tenant_id)})
            headers = {"Authorization": "Bearer " + login.json()["token"]}
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agents["source"].id)})
            session_id = made.json()["id"]
            refused = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={
                "source_key": "must-not-start", "text": "Use the account", "account_selections": [
                    {"target_agent_id": str(agents["middle"].id), "connection_ids": [str(selected)]}]})
            assert refused.status_code == 403, refused.text
            history = await client.get(f"/api/sessions/{session_id}/history", headers=headers)
            assert history.json()["entries"] == []
        assert not calls and not observed


@pytest.mark.parametrize("disable_source", [False, True])
async def test_goal_keeps_original_personal_account_after_logout_and_fails_closed_on_disabled_source(
        test_database, composed_database, tmp_path, monkeypatch, disable_source):  # noqa: F811
    agents, calls, observed = {}, [], []
    base_peer = model_and_mcp(agents, calls, observed)
    app = principal = catalog_id = None
    iterations = 0
    async def peer(request):
        nonlocal iterations
        if request.url.host == "mcp.invalid" or request.method == "GET":
            return base_peer(request)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return base_peer(request)
        assert "Goal-mode execution" in json.dumps(body["messages"])
        mcp = next((name for name in names if name.startswith("mcp_")), None)
        if mcp is None:
            return call("search_tools", "find-mail", {"query": "mailbox"})
        if not any(message.get("tool_call_id") == "mail-read" for message in body["messages"]):
            return call(mcp, "mail-read", {})
        iterations += 1
        if disable_source and iterations == 1:
            await app.state.execution.market.set_enabled(principal, item_id=catalog_id, enabled=False)
        return response({"content": json.dumps({"goal": {"disposition": "continue" if iterations == 1 else "achieved",
            "progress": f"iteration {iterations}", "wake_at": None}})})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, configured_agents, personal = await prepare_accounts(app, test_database.sessions)
        agents.update(configured_agents)
        async with transaction(test_database.sessions) as tx:
            captured = await app.state.execution.tools(tx).capture_authorized(AgentToolResolutionScope(principal.tenant_id, agents["source"].id, "main"))
            catalog_id = next(item.definition.spec.catalog_item_id for item in captured.tools if item.definition.spec.source == "mcp")
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="goal-person", password="password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"login_name": "goal-person", "password": "password", "tenant_id": str(principal.tenant_id)})
            headers = {"Authorization": "Bearer " + login.json()["token"]}
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agents["source"].id)})
            session_id = UUID(made.json()["id"])
            accepted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={
                "source_key": "goal-personal", "text": "/goal Check my mailbox across iterations", "account_selections": [
                    {"target_agent_id": str(agents["source"].id), "connection_ids": [str(personal["source"])]}]})
            assert accepted.status_code == 202 and accepted.json()["error"] is None, accepted.text
            assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
            assert (await client.get(f"/api/sessions/{session_id}/goal", headers=headers)).status_code == 401
            async with asyncio.timeout(15):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        goal = await SessionService(tx).get_goal(principal, session_id=session_id)
                    if not goal.enabled:
                        break
                    await asyncio.sleep(.02)
            assert goal.stopped_reason == ("admission_failed" if disable_source else "achieved")
            assert iterations == (1 if disable_source else 2)
            assert calls == [("/source/personal", "Bearer personal-source")] * iterations


async def test_disabled_source_after_input_acceptance_cannot_fallback_for_new_a2a_target(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    agents, calls, observed = {}, [], []
    base_peer = model_and_mcp(agents, calls, observed)
    app = principal = catalog_id = None
    disabled = False
    async def peer(request):
        nonlocal disabled
        result = base_peer(request)
        if request.url.host != "mcp.invalid" and request.method != "GET" and not disabled:
            payload = result.json()
            tool_calls = payload["choices"][0]["message"].get("tool_calls", [])
            if any(item["function"]["name"] == "send_message_to_agent" for item in tool_calls):
                await app.state.execution.market.set_enabled(principal, item_id=catalog_id, enabled=False)
                disabled = True
        return result
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, configured_agents, personal = await prepare_accounts(app, test_database.sessions)
        agents.update(configured_agents)
        async with transaction(test_database.sessions) as tx:
            captured = await app.state.execution.tools(tx).capture_authorized(AgentToolResolutionScope(principal.tenant_id, agents["source"].id, "main"))
            catalog_id = next(item.definition.spec.catalog_item_id for item in captured.tools if item.definition.spec.source == "mcp")
            session = await SessionService(tx).create(principal, agent_id=agents["source"].id)
        intake = await app.state.products.submit_session(principal, session_id=session.id, source_key="disabled-after-accept",
            input=InputContent("Read mailbox then delegate"), account_selections=(
                PersonalAccountSelection(agents["source"].id, (personal["source"],)),
                PersonalAccountSelection(agents["middle"].id, (personal["middle"],))))
        await eventually(test_database.sessions, principal.tenant_id, intake.run.id, "Completed")
        async with transaction(test_database.sessions) as tx:
            history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=intake.run.id)
            result = next(entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload)
                and entry.payload.result.call_id == "forward")
            output = json.loads(result.content_json)
            assert not output["accepted"] and output["error"] == "not_found"
            request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=UUID(output["request_id"]))
            assert request.admission == "failed" and request.target_run_id is None
            assert request.delegated_connection_ids == (personal["middle"],)
        assert calls == [("/source/personal", "Bearer personal-source")]
