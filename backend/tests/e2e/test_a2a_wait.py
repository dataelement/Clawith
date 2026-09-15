"""A2A waits release the source Run until the independent target supplies input."""

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
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, RunService, WaitingPayload
from app.modules.session.public import SessionService


@pytest.mark.parametrize("early_result", [False, True])
async def test_actual_a2a_wait_suspends_without_human_question_then_resumes_from_result(
        test_database, composed_database, tmp_path, monkeypatch, early_result):  # noqa: F811
    target_finish, target_seen = asyncio.Event(), asyncio.Event()
    target_id = None

    async def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        messages = body["messages"]
        if any(message["role"] == "user" and "initial_input:a2a:" in json.dumps(message["content"]) for message in messages):
            target_seen.set()
            if not early_result:
                await target_finish.wait()
            return response({"content": "TARGET_FINISHED"})
        results = {message.get("tool_call_id"): message["content"] for message in messages if message["role"] == "tool"}
        if "send_message_to_agent" not in names:
            return call("search_tools", "find", {"query": "send_message_to_agent"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"action": "send", "target_agent_id": str(target_id),
                "intent": "consult", "text": "Research independently"})
        if "await" not in results:
            accepted = json.loads(results["send"])
            if early_result:
                async with asyncio.timeout(5):
                    while True:
                        async with transaction(test_database.sessions) as tx:
                            current = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=UUID(accepted["request_id"]))
                        if current.result is not None and current.result["kind"] == "terminal":
                            break
                        await asyncio.sleep(.01)
            return call("send_message_to_agent", "await", {"action": "wait", "request_id": accepted["request_id"]})
        assert "TARGET_FINISHED" in json.dumps(messages), "Source resumed without correlated target input"
        return response({"content": "SOURCE_FINISHED"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Researcher", soul="Research independently",
                timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target.id)
            await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
            session = await SessionService(tx).create(principal, agent_id=source.id)
        try:
            intake = await app.state.products.submit_session(principal, session_id=session.id, source_key="ask",
                input=InputContent("Ask the research Agent, then wait"))
            assert intake.error is None
            await asyncio.wait_for(target_seen.wait(), 10)
            await eventually(test_database.sessions, principal.tenant_id, intake.run.id,
                "Completed" if early_result else "Waiting")
            async with transaction(test_database.sessions) as tx:
                history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=intake.run.id)
                waits = [entry.payload for entry in history.entries if isinstance(entry.payload, WaitingPayload)]
                if early_result:
                    assert not waits
                else:
                    assert len(waits) == 1 and waits[0].related_wait and not waits[0].question
                assert len((await SessionService(tx).read_history(principal, session_id=session.id)).entries) == 1
            target_finish.set()
            await eventually(test_database.sessions, principal.tenant_id, intake.run.id, "Completed")
        finally:
            target_finish.set()


async def test_new_session_main_explicitly_answers_and_receives_existing_a2a_target(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    target_id, request_id, waiting_reference = None, None, None

    async def provider(request):
        nonlocal request_id
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        messages = body["messages"]
        if any(message["role"] == "user" and "initial_input:a2a:" in json.dumps(message["content"]) for message in messages):
            if "PUBLIC_RESPONSE" in json.dumps(messages):
                return response({"content": "COMPLETED_EXISTING_TARGET"})
            return call("need_input", "ask-human", {"question": "Which records?"})
        results = {message.get("tool_call_id"): message["content"] for message in messages if message["role"] == "tool"}
        if "send_message_to_agent" not in names:
            return call("search_tools", "find", {"query": "send_message_to_agent"})
        if "TAKEOVER_NOW" in json.dumps(messages):
            if "answer" not in results:
                return call("send_message_to_agent", "answer", {"action": "answer", "request_id": str(request_id),
                    "waiting_reference": waiting_reference, "text": "PUBLIC_RESPONSE"})
            if "await" not in results:
                return call("send_message_to_agent", "await", {"action": "wait", "request_id": str(request_id)})
            assert "COMPLETED_EXISTING_TARGET" in json.dumps(messages)
            return response({"content": "New Main has the result"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"action": "send", "target_agent_id": str(target_id),
                "intent": "consult", "text": "Research records"})
        request_id = UUID(json.loads(results["send"])["request_id"])
        return response({"content": "Original Main finished without waiting"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Researcher", soul="Ask for records", timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target.id)
            await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
            session = await SessionService(tx).create(principal, agent_id=source.id)
        original = await app.state.products.submit_session(principal, session_id=session.id, source_key="original",
            input=InputContent("Start research then finish this Main"))
        await eventually(test_database.sessions, principal.tenant_id, original.run.id, "Completed")
        async with transaction(test_database.sessions) as tx:
            accepted = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
        target_run = await eventually(test_database.sessions, principal.tenant_id, accepted.target_run_id, "Waiting")
        waiting_reference = target_run.waiting_reference
        current = await app.state.products.submit_session(principal, session_id=session.id, source_key="takeover",
            input=InputContent(f"TAKEOVER_NOW answer request {request_id} with PUBLIC_RESPONSE"))
        assert current.run.id != original.run.id
        await eventually(test_database.sessions, principal.tenant_id, current.run.id, "Completed")
        async with transaction(test_database.sessions) as tx:
            request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
            assert request.target_run_id == target_run.id and request.delivery_run_id == current.run.id
            assert request.source_run_id == original.run.id
            assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=original.run.id)).status == "Completed"
