import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.infrastructure.errors import AccessDenied
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.teams import TeamsAdapter
from app.modules.channel.reply_context import ReplyContext
from app.modules.credential.public import Secret

NOW = datetime(2026, 9, 9, tzinfo=UTC)
SECRET = Secret('{"version":1,"client_secret":"client-secret"}')


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def test_real_rsa_authentication_binds_service_url_and_oauth_reply():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwks = {"keys":[{"kid":"key","kty":"RSA","n":b64(numbers.n.to_bytes(256)),"e":b64(numbers.e.to_bytes(3))}]}
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "teams", "app-id", uuid4(), True, "agent", agent,
        '{"tenant_id":"botframework.com"}')
    service = "https://smba.trafficmanager.net/emea/"
    claims = {"iss":"https://api.botframework.com", "aud":"app-id", "nbf":int(NOW.timestamp())-1,
        "exp":int(NOW.timestamp())+3600,"serviceurl":service}
    header = b64(json.dumps({"alg":"RS256","kid":"key"}).encode())
    body = b64(json.dumps(claims).encode())
    signature = b64(key.sign((header+"."+body).encode(), padding.PKCS1v15(), hashes.SHA256()))
    authorization = {"Authorization":"Bearer "+header+"."+body+"."+signature}
    activity = {"type":"message", "serviceUrl":service,"id":"message","text":"hello",
        "conversation":{"id":"conversation","conversationType":"personal"},"from":{"id":"human"}}
    requests = []
    def peer(request):
        requests.append(request)
        if request.url.host == "login.botframework.com":
            return httpx.Response(200, json=jwks)
        if request.url.host == "login.microsoftonline.com":
            assert b"client_secret=client-secret" in request.content
            return httpx.Response(200, json={"access_token":"access-secret"})
        assert request.url == service + "v3/conversations/conversation/activities"
        assert request.headers["authorization"] == "Bearer access-secret"
        assert json.loads(request.content) == {"type":"message","text":"answer"}
        return httpx.Response(201, json={"id":"reply"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = TeamsAdapter(http)
        result = await adapter.receive(channel, SECRET, body=json.dumps(activity).encode(), headers=authorization, now=NOW)
        assert result.message.actor_id == "human" and result.message.group_id is None
        outcome = await adapter.send(channel, SECRET, destination="conversation", content=DeliveryContent("answer"),
            delivery_key="key", reply_context=result.private_context)
        assert outcome.status == "delivered" and len(requests) == 3
        activity["serviceUrl"] = "https://attacker.example/"
        with pytest.raises(AccessDenied, match="not authenticated"):
            await adapter.receive(channel, SECRET, body=json.dumps(activity).encode(), headers=authorization, now=NOW)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel, SECRET, body=json.dumps(activity).encode(), headers={}, now=NOW)


async def test_teams_cannot_send_to_caller_selected_destination_without_authenticated_context():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "teams", "app", uuid4(), True, "agent", agent)
    def peer(request):
        raise AssertionError("No network before authenticated reply coordinates")
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await TeamsAdapter(http).send(channel, SECRET, destination="https://attacker.example",
            content=DeliveryContent("answer"), delivery_key="key")
    assert result.status == "failed" and result.error == "teams_authenticated_reply_context_required"


async def test_unicode_fragments_share_one_operation_owned_oauth_token():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "teams", "app", uuid4(), True, "agent", agent,
        '{"tenant_id":"botframework.com"}')
    context = ReplyContext(provider="teams", conversation_id="chat", service_url="https://smba.trafficmanager.net/emea/")
    tokens, texts = [], []
    def peer(request):
        if request.url.host == "login.microsoftonline.com":
            tokens.append(request)
            return httpx.Response(200, json={"access_token":"access"})
        texts.append(json.loads(request.content)["text"])
        assert len(texts[-1].encode()) <= 28000
        return httpx.Response(201, json={"id":str(len(texts))})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await TeamsAdapter(http).send(channel, SECRET, destination="chat", content=DeliveryContent("中" * 10000),
            delivery_key="long", reply_context=context)
    assert result.status == "delivered" and len(tokens) == 1 and len(texts) == 2
    assert "".join(texts) == "中" * 10000
