"""Application-owned scheduling and Goal intake with controlled remote Provider HTTP."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.group.public import GroupService
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, RunService, ToolResultPayload, WaitingPayload
from app.modules.session.public import SessionService
from app.modules.trigger.public import TriggerConfig, TriggerService


def provider_factory(observed, *, fail=False):
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        return httpx.Response(503) if fail else response({"content": "Finished"})
    return provider


async def test_unattended_trigger_retains_missing_information_result_without_human_wait(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        assert not any(tool["function"]["name"] == "need_input" for tool in body.get("tools", []))
        if not any(message["role"] == "tool" for message in body["messages"]):
            return call("send_message", "no-destination", {"text": "Missing account information"})
        return response({"content": "Missing required account information; work cannot continue."})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await TriggerService(tx).create(p, agent_id=agent.id,
                config=TriggerConfig("unattended", "interval", "Inspect account", interval_minutes=1))
        occurrence = await app.state.scheduled.fire_manual(p, trigger_id=target.id, event_id="attempt")
        await eventually(test_database.sessions, p.tenant_id, occurrence.run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            stored = await TriggerService(tx).get_occurrence(tenant_id=p.tenant_id, occurrence_id=occurrence.id)
            history = await RunService(tx).read_history(tenant_id=p.tenant_id, run_id=occurrence.run_id)
            snapshot = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=occurrence.run_id)
            assert not (await SessionService(tx).list(p)).sessions
        assert not snapshot.allow_human_input and stored.result.status == "Completed"
        assert "Missing required account information" in stored.result.output_preview
        assert not any(isinstance(entry.payload, WaitingPayload) for entry in history.entries)
        attempt = next(entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload))
        assert attempt.status == "error"


async def test_real_session_group_postcommit_hooks_and_clock_heartbeat_keep_scopes(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider_factory(observed)), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        now = datetime.now(UTC)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
            target = await TriggerService(tx).create(p, agent_id=agent.id, config=TriggerConfig(
                "incoming", "on_message", "Inspect only received input", source_membership_id=p.membership_id), now=now)
            group = await GroupService(tx).create(p, name="Private group")
            await GroupService(tx).set_agent(p, group_id=group.id, agent_id=agent.id, enabled=True)
            await HeartbeatService(tx).configure(p, agent_id=agent.id, config=HeartbeatConfig("Independent heartbeat", 1), now=now)
        direct = await app.state.products.submit_session(p, session_id=session.id, source_key="direct",
            input=InputContent("Direct private message"))
        assert direct.error is None
        grouped = await app.state.products.other.submit_group(p, group_id=group.id, source_key="group",
            input=InputContent("Group private message"), agent_ids=(agent.id,))
        assert not grouped.errors
        app.state.scheduled._clock = lambda: now + timedelta(minutes=1)
        await app.state.scheduled.tick()
        async with asyncio.timeout(10):
            while True:
                async with transaction(test_database.sessions) as tx:
                    events = await TriggerService(tx).history(p, trigger_id=target.id)
                    heartbeats = await HeartbeatService(tx).history(p, agent_id=agent.id)
                if len(events.items) == 2 and len(heartbeats.items) == 1 and all(row.result for row in (*events.items, *heartbeats.items)):
                    break
                await asyncio.sleep(.02)
        async with transaction(test_database.sessions) as tx:
            snapshots = [await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=row.run_id) for row in events.items]
            assert {snap.workspace.output.kind for snap in snapshots} == {"membership", "group"}
            assert all(not snap.workspace.allow_shared_memory_writes for snap in snapshots)
            heartbeat = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=heartbeats.items[0].run_id)
            assert heartbeat.workspace.output.kind == "agent"
        await app.state.products.submit_session(p, session_id=session.id, source_key="direct", input=InputContent("changed retry"))
        async with transaction(test_database.sessions) as tx:
            assert len((await TriggerService(tx).history(p, trigger_id=target.id)).items) == 2


async def test_goal_stops_after_three_provider_attempts_without_restarting_failed_run(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider_factory(observed, fail=True)), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
        intake = await app.state.products.submit_session(p, session_id=session.id, source_key="goal",
            input=InputContent("/goal Complete a long investigation"))
        assert intake.error is None
        await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Failed")
        await app.state.products.goal.tick()
        async with transaction(test_database.sessions) as tx:
            goal = await SessionService(tx).get_goal(p, session_id=session.id)
            assert not goal.enabled
            current = await RunService(tx).get(tenant_id=p.tenant_id, run_id=intake.run.id)
            assert current.status == "Failed"
        assert len(observed) == 3


async def test_goal_continue_creates_new_run_and_achieved_stops_intake(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        return response({"content": json.dumps({"goal": {"disposition": "continue" if len(observed) == 1 else "achieved",
            "progress": "First evidence collected" if len(observed) == 1 else "Finished investigation", "wake_at": None}})})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
        intake = await app.state.products.submit_session(p, session_id=session.id, source_key="goal",
            input=InputContent("/goal Finish investigation"))
        await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Completed")
        async with asyncio.timeout(10):
            while True:
                async with transaction(test_database.sessions) as tx:
                    goal = await SessionService(tx).get_goal(p, session_id=session.id)
                if not goal.enabled:
                    break
                await asyncio.sleep(.02)
        assert goal.stopped_reason == "achieved" and len(observed) == 2
        assert "First evidence collected" in json.dumps(observed[1])
        async with transaction(test_database.sessions) as tx:
            work = await SessionService(tx).list_work(p, session_id=session.id)
            assert len(work.work) == 2 and len({item.run_id for item in work.work}) == 2
        await app.state.products.goal.tick()
        assert len(observed) == 2


async def test_received_a2a_triggers_only_target_subscription_without_private_workspace_inheritance(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    target_id = None
    observed = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        if "Target researcher identity" in json.dumps(body["messages"]):
            return response({"content": "Target work finished"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "discover", {"query": "send_message_to_agent"})
        if not any(message.get("tool_call_id") == "send" for message in body["messages"]):
            return call("send_message_to_agent", "send", {"action": "send", "target_agent_id": str(target_id),
                "intent": "notify", "text": "Explicitly supplied target message"})
        return response({"content": "Source finished"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(p, name="Target", soul="Target researcher identity", timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, p, agent_id=target.id)
            await PermissionService(tx).set_visibility(p, agent_id=target.id, visibility="tenant")
            wanted = await TriggerService(tx).create(p, agent_id=target.id,
                config=TriggerConfig("received", "on_message", "Handle only received A2A", source_agent_id=source.id))
            unrelated = await TriggerService(tx).create(p, agent_id=source.id,
                config=TriggerConfig("not target", "on_message", "Must not broadcast", source_agent_id=source.id))
            session = await SessionService(tx).create(p, agent_id=source.id)
        intake = await app.state.products.submit_session(p, session_id=session.id, source_key="a2a",
            input=InputContent("Send a targeted request. Private source workspace must not transfer."))
        await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Completed")
        async with asyncio.timeout(10):
            while True:
                async with transaction(test_database.sessions) as tx:
                    history = await TriggerService(tx).history(p, trigger_id=wanted.id)
                if history.items and history.items[0].result:
                    break
                await asyncio.sleep(.02)
        async with transaction(test_database.sessions) as tx:
            assert len(history.items) == 1
            captured = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=history.items[0].run_id)
            assert captured.workspace.output.kind == "agent" and captured.workspace.output.id == target.id
            assert not captured.workspace.allow_shared_memory_writes
            assert not captured.workspace.allow_shared_file_writes
            assert history.items[0].origin_kind == "membership" and history.items[0].origin_id == p.membership_id
            assert not any(tool.credential and tool.credential.owner_kind == "membership" for tool in captured.tools.tools)
            assert not (await TriggerService(tx).history(p, trigger_id=unrelated.id)).items
            identity = IdentityService(tx)
            account = await identity.create_account()
            membership = await identity.create_membership(tenant_id=p.tenant_id, account_id=account.id,
                display_name="Another Agent viewer", role="tenant_admin")
            other = TenantPrincipal(account.id, membership.id, p.tenant_id, "tenant_admin")
            assert not (await TriggerService(tx).history(other, trigger_id=wanted.id)).items
