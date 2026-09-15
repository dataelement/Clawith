"""Feishu actual HTTP/crypto/wire framing with controlled network peers."""

import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.infrastructure.errors import AccessDenied
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.feishu import FeishuAdapter
from app.modules.credential.public import Secret

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def channel(mode="webhook"):
    return ChannelView(uuid4(), uuid4(), uuid4(), "feishu", "cli-app", uuid4(), True, "agent", uuid4(),
        json.dumps({"bot_open_id": "bot", "tenant_key": "tenant", "connection_mode": mode}))


def credential(encryption=""):
    return Secret(json.dumps({"version": 1, "app_id": "cli-app", "app_secret": "app-secret", "verification_token": "verify", "encrypt_key": encryption}))


def event(*, group=False, mentioned=True, kind="text", content=None):
    return {"schema": "2.0", "header": {"app_id": "cli-app", "tenant_key": "tenant", "token": "verify", "event_id": "event",
        "event_type": "im.message.receive_v1"}, "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "human"}},
        "message": {"message_id": "message", "chat_id": "chat", "chat_type": "group" if group else "p2p", "message_type": kind,
            "content": json.dumps(content or {"text": "@_user_1 hello"}),
            "mentions": [{"key": "@_user_1", "name": "Agent", "id": {"open_id": "bot"}}] if mentioned else []}}}


def encrypted(payload, key):
    raw = json.dumps(payload).encode()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    iv = b"0123456789abcdef"
    encryptor = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(iv)).encryptor()
    body = json.dumps({"encrypt": base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()}).encode()
    stamp, nonce = str(int(NOW.timestamp())), "nonce"
    headers = {"x-lark-request-timestamp": stamp, "x-lark-request-nonce": nonce,
        "x-lark-signature": hashlib.sha256((stamp + nonce + key).encode() + body).hexdigest()}
    return body, headers


async def test_authenticated_encrypted_event_and_plain_challenge():
    async with create_stateless_http_client() as client:
        adapter = FeishuAdapter(client)
        body, headers = encrypted(event(group=True), "aes-key")
        received = await adapter.receive(channel(), credential("aes-key"), body=body, headers=headers, now=NOW)
        assert received.message.text == "@Agent hello" and received.message.group_id == "chat"
        challenge, _ = encrypted({"type": "url_verification", "token": "verify", "challenge": "challenge"}, "aes-key")
        assert (await adapter.receive(channel(), credential("aes-key"), body=challenge, headers={}, now=NOW)).challenge == "challenge"
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential("aes-key"), body=body, headers={**headers, "x-lark-signature": "bad"}, now=NOW)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential("aes-key"), body=body, headers=headers, now=NOW.replace(year=2027))


async def test_identity_bot_loop_group_selection_and_file_reference():
    async with create_stateless_http_client() as client:
        adapter = FeishuAdapter(client)
        raw = event(group=True, mentioned=False)
        assert (await adapter.receive(channel(), credential(), body=json.dumps(raw).encode(), headers={}, now=NOW)).message is None
        raw = event()
        raw["event"]["sender"]["sender_type"] = "app"
        assert (await adapter.receive(channel(), credential(), body=json.dumps(raw).encode(), headers={}, now=NOW)).message is None
        raw["header"]["tenant_key"] = "other"
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential(), body=json.dumps(raw).encode(), headers={}, now=NOW)
        raw = event(kind="file", content={"file_key": "file-key", "file_name": "report.pdf"})
        result = await adapter.receive(channel(), credential(), body=json.dumps(raw).encode(), headers={}, now=NOW)
        assert result.message.attachments[0].external_id == "message/file-key"


@pytest.mark.parametrize("status,expected", [(200, "delivered"), (403, "failed"), (500, "uncertain")])
async def test_actual_http_token_then_message_with_isolated_account_and_ack(status, expected):
    seen = []
    def peer(request):
        seen.append(request)
        assert "cookie" not in request.headers and request.headers.get("x-client-default") is None
        if request.url.path.endswith("/tenant_access_token/internal"):
            assert json.loads(request.content) == {"app_id": "cli-app", "app_secret": "app-secret"}
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"}, headers={"set-cookie": "account=bad"})
        assert request.url.host == "open.feishu.cn" and request.headers["authorization"] == "Bearer tenant-token"
        payload = json.loads(request.content)
        assert payload["receive_id"] == "chat" and json.loads(payload["content"])["text"] == "Hello"
        return httpx.Response(status, json={"code": 0, "data": {"message_id": "sent"}})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        client.headers["x-client-default"] = "must-not-forward"
        result = await FeishuAdapter(client).send(channel(), credential(), destination="chat", content=DeliveryContent("Hello"), delivery_key="delivery")
        assert result.status == expected and len(seen) == 2 and not list(client.cookies.jar)


async def test_long_connection_protobuf_fragment_ack_and_cancellation_cleanup():
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame
    queue, sent, accepted, closed = asyncio.Queue(), [], [], asyncio.Event()
    payload = json.dumps(event()).encode()
    for index, fragment in enumerate((payload[:20], payload[20:])):
        frame = Frame(SeqID=index, LogID=1, service=7, method=1, payload=fragment)
        for key, value in (("type", "event"), ("sum", "2"), ("seq", str(index)), ("message_id", "wire-message")):
            frame.headers.add(key=key, value=value)
        queue.put_nowait(frame.SerializeToString())
    class Socket:
        async def recv(self):
            return await queue.get()
        async def send(self, value):
            frame = Frame()
            frame.ParseFromString(value)
            sent.append(frame)
    @asynccontextmanager
    async def connector(url):
        assert url == "wss://msg-frontier.feishu.cn/socket?service_id=7"
        try:
            yield Socket()
        finally:
            closed.set()
    def peer(request):
        assert request.url.path == "/callback/ws/endpoint"
        return httpx.Response(200, json={"code": 0, "data": {"URL": "wss://msg-frontier.feishu.cn/socket?service_id=7", "ClientConfig": {"PingInterval": 120}}})
    received = asyncio.Event()
    async def consume(message):
        accepted.append(message)
        received.set()
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        task = asyncio.create_task(FeishuAdapter(client, connector=connector).listen(channel("websocket"), credential(), consume))
        try:
            await asyncio.wait_for(received.wait(), timeout=2)
            await asyncio.sleep(0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set() and len(accepted) == 1 and any(frame.payload == b'{"code":200}' for frame in sent)
    assert not any(task.get_name() == "feishu-channel-ping" for task in asyncio.all_tasks())


async def test_unavailable_channel_and_opaque_attachments_fail_before_external_io():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("No send"))) as client:
        adapter = FeishuAdapter(client)
        assert (await adapter.send(replace(channel(), enabled=False), credential(), destination="chat", content=DeliveryContent("x"), delivery_key="key")).status == "failed"
        assert (await adapter.send(channel(), credential(), destination="chat", content=DeliveryContent("x", ("workspace:opaque",)), delivery_key="key")).status == "failed"


async def test_file_wire_ports_and_localized_rich_post_preserve_resources():
    observed = []
    async def peer(request):
        observed.append(request.url.path)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/files"):
            body = await request.aread()
            assert b"file-content" in body and b"report.txt" in body
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "file-key"}})
        if "/resources/" in request.url.path:
            assert request.url.params["type"] == "file"
            return httpx.Response(200, content=b"file-content", headers={"content-type": "text/plain"})
        assert json.loads(request.content)["msg_type"] == "file"
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent-file"}})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        adapter = FeishuAdapter(client)
        key = await adapter.upload_file(channel(), credential(), filename="report.txt", content=b"file-content")
        assert key == "file-key"
        assert (await adapter.send_file(channel(), credential(), destination="chat", file_key=key, delivery_key="file-send")).status == "delivered"
        body, mime = await adapter.download_resource(channel(), credential(), message_id="message", resource_key=key, resource_type="file")
        assert body == b"file-content" and mime == "text/plain"
        post = event(kind="post", content={"zh_cn": {"title": "Report", "content": [[
            {"tag": "a", "text": "source", "href": "https://example.com"}, {"tag": "img", "image_key": "image"}]]}})
        result = await adapter.receive(channel(), credential(), body=json.dumps(post).encode(), headers={}, now=NOW)
        assert "https://example.com" in result.message.text and result.message.attachments[0].external_id == "message/image"


async def test_heartbeat_send_failure_closes_connection_without_a_stranded_reader():
    closed = asyncio.Event()
    class Socket:
        async def recv(self):
            await asyncio.Future()
        async def send(self, raw):
            raise OSError("socket failed")
    @asynccontextmanager
    async def connector(url):
        try:
            yield Socket()
        finally:
            closed.set()
    def peer(request):
        return httpx.Response(200, json={"code": 0, "data": {"URL": "wss://msg-frontier.feishu.cn/socket?service_id=7"}})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        async def consume(result):
            pytest.fail("No message")
        from app.modules.channel.public import ListenerDisconnected
        with pytest.raises(ListenerDisconnected):
            await asyncio.wait_for(FeishuAdapter(client, connector=connector).listen(channel("websocket"), credential(), consume), 2)
    assert closed.is_set() and not any(task.get_name().startswith("feishu-channel") for task in asyncio.all_tasks())
