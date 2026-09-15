import asyncio

import httpx
from e2e.test_runtime_product_owner_fixture import configure_agent
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.run.public import InputContent
from app.modules.session.public import SessionService


async def test_real_websocket_route_replays_committed_history_and_closes_on_logout(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="person", password="password")
        token, principal = await app.state.auth.login("person", "password", principal.tenant_id)
        async with transaction(test_database.sessions) as tx:
            service = SessionService(tx)
            session = await service.create(principal, agent_id=agent.id)
            await service.accept_input(principal, session_id=session.id, source_key="saved", input=InputContent("Saved message"))
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "scheme": "ws",
            "path": f"/api/sessions/{session.id}/events", "raw_path": f"/api/sessions/{session.id}/events".encode(),
            "query_string": b"after_position=0", "root_path": "", "server": ("test", 80), "client": ("test", 1234),
            "headers": [(b"sec-websocket-protocol", f"clawith, auth.{token}".encode())], "subprotocols": ["clawith", f"auth.{token}"]}
        connection = asyncio.create_task(app(scope, incoming.get, outgoing.put))
        try:
            async with asyncio.timeout(5):
                assert (await outgoing.get())["type"] == "websocket.accept"
                replay = await outgoing.get()
                assert replay["type"] == "websocket.send" and "Saved message" in replay["text"]
                await app.state.auth.logout(token)
                closed = await outgoing.get()
                assert closed["type"] == "websocket.close" and closed["code"] == 1008
                await connection
        finally:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
        assert connection.done()
