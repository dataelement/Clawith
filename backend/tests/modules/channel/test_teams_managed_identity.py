import asyncio
from uuid import uuid4

import aiohttp
import httpx
import pytest
from aiohttp import web
from azure.core.pipeline.transport import AioHttpTransport
from azure.identity.aio import ManagedIdentityCredential

from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.teams import TeamsAdapter
from app.modules.channel.reply_context import ReplyContext
from app.modules.credential.public import Secret


@pytest.mark.parametrize("mode", ["success","denied","cancelled"])
async def test_real_managed_identity_sdk_closes_its_transport_on_all_outcomes(monkeypatch, mode):
    entered, release = asyncio.Event(), asyncio.Event()
    requests, sessions = [], []
    async def metadata(request):
        requests.append(request)
        assert request.headers["X-IDENTITY-HEADER"] == "metadata-secret"
        assert request.query["client_id"] == "managed-client"
        assert request.query["resource"] == "https://api.botframework.com"
        entered.set()
        if mode == "cancelled":
            await release.wait()
        if mode == "denied":
            return web.json_response({"error":"unauthorized","error_description":"Identity is unavailable"}, status=400)
        return web.json_response({"access_token":"managed-token","expires_on":"2100000000",
            "resource":"https://api.botframework.com","token_type":"Bearer"})
    app = web.Application()
    app.router.add_get("/metadata", metadata)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setenv("IDENTITY_ENDPOINT", f"http://127.0.0.1:{port}/metadata")
    monkeypatch.setenv("IDENTITY_HEADER", "metadata-secret")
    monkeypatch.delenv("IDENTITY_SERVER_THUMBPRINT", raising=False)
    def factory(client_id):
        session = aiohttp.ClientSession()
        sessions.append(session)
        return ManagedIdentityCredential(client_id=client_id, transport=AioHttpTransport(session=session, session_owner=True))
    bot_calls = []
    def bot(request):
        bot_calls.append(request)
        assert request.headers["authorization"] == "Bearer managed-token"
        return httpx.Response(201, json={"id":"reply"})
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "teams", "app", uuid4(), True, "agent", agent,
        '{"tenant_id":"botframework.com"}')
    context = ReplyContext(provider="teams", conversation_id="chat", service_url="https://smba.trafficmanager.net/emea/")
    try:
        async with create_stateless_http_client(transport=httpx.MockTransport(bot)) as http:
            operation = asyncio.create_task(TeamsAdapter(http, managed_identity_factory=factory).send(channel,
                Secret('{"version":1,"managed_identity_client_id":"managed-client"}'), destination="chat",
                content=DeliveryContent("answer"), delivery_key="key", reply_context=context))
            await asyncio.wait_for(entered.wait(), timeout=3)
            if mode == "cancelled":
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                result = await operation
                assert result.status == ("delivered" if mode == "success" else "failed")
        assert len(sessions) == 1 and sessions[0].closed
        assert len(bot_calls) == (1 if mode == "success" else 0)
    finally:
        release.set()
        await runner.cleanup()
