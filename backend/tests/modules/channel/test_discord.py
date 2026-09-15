import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from app.infrastructure.errors import AccessDenied
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.discord import DiscordAdapter
from app.modules.channel.reply_context import ReplyContext
from app.modules.credential.public import Secret

NOW = datetime(2026, 9, 9, tzinfo=UTC)
SECRET = Secret('{"version":1,"bot_token":"secret"}')


async def test_discord_authentication_ping_and_private_slash_token():
    key = Ed25519PrivateKey.generate()
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent,
        json.dumps({"connection_mode":"webhook", "public_key":key.public_key().public_bytes_raw().hex()}))
    def signed(payload):
        raw = json.dumps(payload).encode()
        stamp = str(int(NOW.timestamp()))
        return raw, {"x-signature-timestamp":stamp,"x-signature-ed25519":key.sign(stamp.encode()+raw).hex()}
    async with create_stateless_http_client() as http:
        adapter = DiscordAdapter(http)
        body, headers = signed({"type":1,"application_id":"123"})
        assert (await adapter.receive(channel, SECRET, body=body, headers=headers, now=NOW)).reply.body == '{"type":1}'
        with pytest.raises(AccessDenied):
            await adapter.receive(channel, SECRET, body=body+b" ", headers=headers, now=NOW)
        payload = {"type":2,"application_id":"123","id":"456","channel_id":"789","token":"private-interaction",
            "data":{"name":"ask","options":[{"type":3,"name":"message","value":"hello"}]},"user":{"id":"321"}}
        body, headers = signed(payload)
        result = await adapter.receive(channel, SECRET, body=body, headers=headers, now=NOW)
        assert result.message.text == "hello" and result.message.actor_id == "321"
        assert "private-interaction" not in repr(result)
        assert result.private_context.reply_token.get_secret_value() == "private-interaction"
        assert result.reply.body == '{"type":5}'
        payload["application_id"] = "999"
        body, headers = signed(payload)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel, SECRET, body=body, headers=headers, now=NOW)


@pytest.mark.parametrize("status,expected", [(200,"delivered"),(403,"failed"),(500,"uncertain")])
async def test_bot_http_delivery_real_wire_and_outcomes(status, expected):
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    requests = []
    def peer(request):
        requests.append(request)
        assert request.url.path == "/api/v10/channels/789/messages"
        assert request.headers["authorization"] == "Bot secret"
        assert json.loads(request.content) == {"content":"hello","allowed_mentions":{"parse":[]}}
        return httpx.Response(status, json={"id":"456","channel_id":"789"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await DiscordAdapter(http).send(channel, SECRET, destination="789", content=DeliveryContent("hello"), delivery_key="key")
    assert result.status == expected and len(requests) == 1


async def test_slash_reply_edits_original_with_private_token_not_bot_send(caplog):
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    context = ReplyContext(provider="discord", conversation_id="789", reply_token=SecretStr("private-token"))
    def peer(request):
        assert request.method == "PATCH"
        assert request.url.raw_path == b"/api/v10/webhooks/123/private-token/messages/@original"
        assert "authorization" not in request.headers
        assert "private-token" not in str(request.url)
        return httpx.Response(200, json={"id":"456","channel_id":"789"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await DiscordAdapter(http).send(channel, SECRET, destination="789",
            content=DeliveryContent("answer"), delivery_key="key", reply_context=context, reply_operation="original")
    assert result.status == "delivered"
    assert "private-token" not in caplog.text


async def test_register_ask_uses_actual_application_command_endpoint():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    def peer(request):
        assert request.method == "PUT" and request.url.path == "/api/v10/applications/123/commands"
        command, = json.loads(request.content)
        assert command["name"] == "ask" and command["options"][0]["name"] == "message"
        assert request.headers["authorization"] == "Bot secret"
        return httpx.Response(200, json=[{"id":"command","name":"ask","application_id":"123"}])
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        await DiscordAdapter(http).register_commands(channel, SECRET)


async def test_long_original_reply_uses_followups_for_later_fragments():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    context = ReplyContext(provider="discord", conversation_id="789", reply_token=SecretStr("token"))
    calls = []
    def peer(request):
        calls.append(request)
        assert request.method == ("PATCH" if len(calls) == 1 else "POST")
        return httpx.Response(200, json={"id":str(len(calls)),"channel_id":"789"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        outcome = await DiscordAdapter(http).send(channel, SECRET, destination="789", content=DeliveryContent("🙂" * 4001),
            delivery_key="long", reply_context=context, reply_operation="original")
    assert outcome.status == "delivered"
    assert [len(json.loads(request.content)["content"]) for request in calls] == [2000,2000,1]


async def test_file_uses_multipart_original_reply_and_preserves_private_token():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    context = ReplyContext(provider="discord", conversation_id="789", reply_token=SecretStr("private-token"))
    calls = []
    def peer(request):
        calls.append(request)
        assert request.method == "PATCH" and request.url.raw_path.endswith(b"/messages/@original")
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="files[0]"; filename="report.txt"' in request.content
        assert b"actual-file-content" in request.content and "authorization" not in request.headers
        assert "private-token" not in repr(request.url)
        return httpx.Response(200, json={"id":"456","channel_id":"789"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        outcome = await DiscordAdapter(http).send_file(channel, SECRET, destination="789", filename="report.txt",
            content=b"actual-file-content", reply_context=context, reply_operation="original")
    assert outcome.status == "delivered" and len(calls) == 1


async def test_attachment_download_resolves_captured_native_ids_without_forwarding_bot_token():
    agent = uuid4()
    channel = ChannelView(uuid4(), uuid4(), agent, "discord", "123", uuid4(), True, "agent", agent)
    calls = []
    def peer(request):
        calls.append(request)
        if request.url.host == "discord.com":
            assert request.url.path == "/api/v10/channels/789/messages/456"
            assert request.headers["authorization"] == "Bot secret"
            return httpx.Response(200, json={"id":"456","channel_id":"789","attachments":[{
                "id":"321","url":"https://cdn.discordapp.com/attachments/image.png?secret=private-token"}]})
        assert request.url.host == "cdn.discordapp.com" and "authorization" not in request.headers
        assert "private-token" not in str(request.url)
        return httpx.Response(200, content=b"image")
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        assert await DiscordAdapter(http).download_resource(channel, SECRET, reference="789/456/321", maximum=5) == b"image"
    assert len(calls) == 2
