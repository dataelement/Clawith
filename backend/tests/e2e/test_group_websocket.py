"""Real ASGI Group subscriptions replay committed, conversation-scoped history."""

import asyncio
import json

import httpx
from e2e.test_runtime_product_owner_fixture import configure_agent
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.group.public import GroupService
from app.modules.identity_tenant.public import IdentityService
from app.modules.run.public import InputContent


def connect(app, group_id, conversation_id, token, *, after=0):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    incoming.put_nowait({"type": "websocket.connect"})
    path = f"/api/groups/{group_id}/events"
    scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "scheme": "ws",
        "path": path, "raw_path": path.encode(), "query_string": f"conversation_id={conversation_id}&after_position={after}".encode(),
        "root_path": "", "server": ("test", 80), "client": ("test", 1234),
        "headers": [(b"sec-websocket-protocol", f"clawith, auth.{token}".encode())], "subprotocols": ["clawith", f"auth.{token}"]}
    return asyncio.create_task(app(scope, incoming.get, outgoing.put)), incoming, outgoing


async def test_group_socket_committed_topic_replay_new_messages_and_logout(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
            {"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, _, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="group-person", password="password")
        token, principal = await app.state.auth.login("group-person", "password", principal.tenant_id)
        async with transaction(test_database.sessions) as tx:
            service = GroupService(tx)
            group = await service.create(principal, name="Group")
            topic = await service.create_conversation(principal, group_id=group.id, title="Topic")
            await service.accept_input(principal, group_id=group.id, source_key="other", input=InputContent("OTHER_TOPIC"), agent_ids=())
            saved = await service.accept_input(principal, group_id=group.id, conversation_id=topic.id,
                source_key="saved", input=InputContent("SAVED_TOPIC"), agent_ids=())
        task, incoming, outgoing = connect(app, group.id, topic.id, token)
        try:
            async with asyncio.timeout(5):
                assert (await outgoing.get())["type"] == "websocket.accept"
                replay = await outgoing.get()
                assert "SAVED_TOPIC" in replay["text"] and "OTHER_TOPIC" not in replay["text"]
                assert json.loads(replay["text"])["next_after_position"] == saved.event.position
            async with transaction(test_database.sessions) as tx:
                await GroupService(tx).accept_input(principal, group_id=group.id, conversation_id=topic.id,
                    source_key="next", input=InputContent("COMMITTED_NEXT"), agent_ids=())
                await asyncio.sleep(1.1)
                assert outgoing.empty(), "Uncommitted Group input leaked through the stream"
            async with asyncio.timeout(5):
                assert "COMMITTED_NEXT" in (await outgoing.get())["text"]
                await app.state.auth.logout(token)
                closed = await outgoing.get()
                assert closed["type"] == "websocket.close" and closed["code"] == 1008
                await task
        finally:
            incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        async with transaction(test_database.sessions) as tx:
            identities = IdentityService(tx)
            account = await identities.create_account()
            await identities.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
                display_name="Outsider", role="member")
        await app.state.auth.provision_trusted_verifier(account_id=account.id, login_name="outsider", password="password")
        outsider_token, _ = await app.state.auth.login("outsider", "password", principal.tenant_id)
        task, incoming, outgoing = connect(app, group.id, topic.id, outsider_token)
        try:
            async with asyncio.timeout(5):
                assert (await outgoing.get())["type"] == "websocket.close"
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_group_socket_exits_with_1001_on_real_application_shutdown(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
            {"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    task = None
    tasks_before = set(asyncio.all_tasks())
    try:
        async with app.router.lifespan_context(app):
            principal, _, _ = await configure_agent(app.state.execution, test_database.sessions)
            await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="closing-person", password="password")
            token, principal = await app.state.auth.login("closing-person", "password", principal.tenant_id)
            async with transaction(test_database.sessions) as tx:
                service = GroupService(tx)
                group = await service.create(principal, name="Shutdown")
                topic = await service.resolve_conversation(principal, group_id=group.id)
                await service.accept_input(principal, group_id=group.id, source_key="saved", input=InputContent("Saved"), agent_ids=())
            task, _, outgoing = connect(app, group.id, topic, token)
            async with asyncio.timeout(5):
                assert (await outgoing.get())["type"] == "websocket.accept"
                assert (await outgoing.get())["type"] == "websocket.send"
        async with asyncio.timeout(5):
            closed = await outgoing.get()
            assert closed["type"] == "websocket.close" and closed["code"] == 1001
            await task
        assert task.done()
        assert not [item for item in asyncio.all_tasks() - tasks_before if not item.done()
            and item.get_name().startswith("product-websocket-")]
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
