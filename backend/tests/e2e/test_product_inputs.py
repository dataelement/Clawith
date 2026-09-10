"""Real application product services and Runtime; only remote Model HTTP is controlled."""

import asyncio
import json
from uuid import UUID

import httpx
import pytest
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService
from app.modules.agent.public import AgentService
from app.modules.group.public import GroupService
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, RelatedInputPayload, RunService, ToolResultPayload
from app.modules.session.public import SessionService
from app.modules.workspace.public import WorkspaceSubject


@pytest.mark.parametrize("fail_second", [False, True])
async def test_group_multi_target_runs_use_group_scope_and_keep_distinct_messages(
        test_database, composed_database, tmp_path, monkeypatch, fail_second):  # noqa: F811
    observed = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        assert "Group collaboration rule" in json.dumps(body["messages"])
        if "Fail second Group target." in json.dumps(body["messages"]):
            return httpx.Response(401)
        completed = [message for message in body["messages"] if message["role"] == "tool"]
        return response({"content": "Independent Group outcome"}) if completed else call("send_message", "answer", {"text": "Group report ready."})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, first, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            second = await AgentService(tx).create(principal, name="Researcher",
                soul="Fail second Group target." if fail_second else "Research independently.",
                timezone="UTC", model_id=first.model_id)
            await provision_builtin_tools(tx, principal, agent_id=second.id)
            group = await GroupService(tx).create(principal, name="Research", announcement="Group collaboration rule")
            await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=first.id, enabled=True)
            await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=second.id, enabled=True)
        intake = await app.state.products.other.submit_group(principal, group_id=group.id, source_key="group-work",
            input=InputContent("Prepare the Group report."), agent_ids=(first.id, second.id))
        assert not intake.errors and len(intake.runs) == 2
        for run in intake.runs:
            await eventually(test_database.sessions, principal.tenant_id, run.id,
                "Failed" if fail_second and run.agent_id == second.id else "Completed")
        duplicate = await app.state.products.other.submit_group(principal, group_id=group.id, source_key="group-work",
            input=InputContent("Do not rewrite accepted work."), agent_ids=(first.id,))
        assert {run.id for run in duplicate.runs} == {run.id for run in intake.runs}
        async with transaction(test_database.sessions) as tx:
            events = await GroupService(tx).list_events(principal, group_id=group.id)
            links = await GroupService(tx).links(principal, group_id=group.id, event_id=intake.accepted.event.id)
            assert len(events) == (2 if fail_second else 3)
            assert {event.agent_id for event in events if event.kind == "reply"} == ({first.id} if fail_second else {first.id, second.id})
            assert all(link.result["status"] == ("Failed" if fail_second and link.agent_id == second.id else "Completed") for link in links)
            for run in intake.runs:
                snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=run.id)
                assert snapshot.workspace.output.kind == "group" and snapshot.workspace.output.id == group.id
                assert not snapshot.workspace.allow_shared_memory_writes and snapshot.agent_id == run.agent_id
        assert len(observed) == (3 if fail_second else 4)
        other = app.state.products.other
    assert other._task.done() and not other._deliveries


@pytest.mark.parametrize("cancel_source", [False, True])
async def test_actual_a2a_tool_keeps_independent_target_and_private_memory_boundary(
        test_database, composed_database, tmp_path, monkeypatch, cancel_source):  # noqa: F811
    target_seen, target_finish, source_finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    target_id = None

    async def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        messages = body["messages"]
        target = any(message["role"] == "user" and "initial_input:a2a:" in json.dumps(message["content"]) for message in messages)
        completed = {message.get("tool_call_id") for message in messages if message["role"] == "tool"}
        if target:
            target_seen.set()
            if "write-private-memory" not in completed:
                return call("write_file", "write-private-memory", {"workspace": "current", "path": "memory/MEMORY.md",
                    "content": "Private requester detail must not become shared memory.", "expected_revision": None})
            await target_finish.wait()
            return response({"content": "Independent research result"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "find-a2a", {"query": "send_message_to_agent"})
        if "ask-researcher" not in completed:
            return call("send_message_to_agent", "ask-researcher", {"action": "send", "target_agent_id": str(target_id),
                "intent": "consult", "text": "Research this explicitly supplied private request."})
        await source_finish.wait()
        return response({"content": "Source result after consultation"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Independent researcher", soul="Own Agent identity.",
                timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target.id)
            await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
            session = await SessionService(tx).create(principal, agent_id=source.id)
        try:
            intake = await app.state.products.submit_session(principal, session_id=session.id, source_key="consultation",
                input=InputContent("Coordinate private research using the other Agent."))
            assert intake.run is not None and intake.error is None
            await asyncio.wait_for(target_seen.wait(), 10)
            other = app.state.products.other
            async with asyncio.timeout(10):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        source_history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=intake.run.id)
                    accepted_call = next((entry.payload for entry in source_history.entries if isinstance(entry.payload, ToolResultPayload)
                        and entry.payload.result.call_id == "ask-researcher"), None)
                    if accepted_call is not None:
                        assert accepted_call.result.status == "success"
                        request_id = UUID(json.loads(accepted_call.result.content_json)["request_id"])
                        break
                    await asyncio.sleep(.02)
            async with transaction(test_database.sessions) as tx:
                request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
                target_run = await RunService(tx).get(tenant_id=principal.tenant_id, run_id=request.target_run_id)
                captured = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=target_run.id)
                assert target_run.parent_run_id is None and target_run.agent_id == target.id
                assert captured.workspace.output == WorkspaceSubject("agent", target.id)
                assert not captured.workspace.allow_shared_memory_writes
                assert captured.workspace.output.id != principal.membership_id
            if cancel_source:
                await app.state.runtime.cancel(tenant_id=principal.tenant_id, run_id=intake.run.id)
                async with transaction(test_database.sessions) as tx:
                    assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=target_run.id)).status == "Running"
            target_finish.set()
            await eventually(test_database.sessions, principal.tenant_id, target_run.id, "Completed")
            async with asyncio.timeout(10):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        delivered = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
                    if delivered.source_delivery == ("source_terminal" if cancel_source else "accepted"):
                        break
                    await asyncio.sleep(.02)
            source_finish.set()
            if not cancel_source:
                await eventually(test_database.sessions, principal.tenant_id, intake.run.id, "Completed")
            async with transaction(test_database.sessions) as tx:
                history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=target_run.id)
                memory_attempt = next(entry.payload for entry in history.entries if isinstance(entry.payload, ToolResultPayload)
                    and entry.payload.result.call_id == "write-private-memory")
                assert memory_attempt.result.status == "error" and "access_denied" in memory_attempt.result.content_json
                source_history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=intake.run.id)
                deliveries = [entry for entry in source_history.entries if isinstance(entry.payload, RelatedInputPayload)
                    and entry.source.kind == "a2a_result"]
                assert len(deliveries) == (0 if cancel_source else 1)
            assert await app.state.execution.workspace.memory_index(captured.workspace, captured.workspace.output) is None
        finally:
            source_finish.set()
            target_finish.set()
    assert other._task.done() and not other._deliveries
