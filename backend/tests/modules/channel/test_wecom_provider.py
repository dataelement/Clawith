"""WeCom wire-level callbacks, HTTP sends and asynchronous bot acknowledgements."""

import asyncio
import base64
import hashlib
import json
import logging
import struct
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import SecretStr
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.infrastructure.errors import AccessDenied
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers import wecom
from app.modules.channel.providers.wecom import WeComAdapter, customer_service_notice
from app.modules.channel.reply_context import MediaContext
from app.modules.credential.public import Secret

NOW = datetime(2026, 9, 9, tzinfo=UTC)
KEY = base64.b64encode(b"a" * 32).decode().rstrip("=")


def channel(mode="webhook"):
    settings = {"connection_mode": mode}
    if mode == "webhook":
        settings.update(corp_id="corp", agent_id=7)
    return ChannelView(uuid4(), uuid4(), uuid4(), "wecom", "corp:7" if mode == "webhook" else "bot", uuid4(), True,
        "agent", uuid4(), json.dumps(settings))


def credential(mode="webhook"):
    value = {"version": 1, "bot_secret": "bot-secret"} if mode == "websocket" else {
        "version": 1, "corp_secret": "corp-secret", "verification_token": "verify", "encoding_aes_key": KEY}
    return Secret(json.dumps(value))


def callback(content, *, corp="corp", challenge=False):
    plain = b"0123456789abcdef" + struct.pack("!I", len(content)) + content + corp.encode()
    padder = padding.PKCS7(256).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(b"a" * 32), modes.CBC(b"a" * 16)).encryptor()
    encrypted = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
    stamp, nonce = str(int(NOW.timestamp())), "nonce"
    headers = {"timestamp": stamp, "nonce": nonce,
        "msg_signature": hashlib.sha1("".join(sorted(("verify", stamp, nonce, encrypted))).encode()).hexdigest()}
    if challenge:
        headers["echostr"] = encrypted
    return f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode(), headers


async def test_encrypted_application_callback_and_plaintext_verification_response():
    message = b"<xml><ToUserName>corp</ToUserName><FromUserName>human</FromUserName><MsgId>message</MsgId><AgentID>7</AgentID><MsgType>text</MsgType><Content>hello</Content></xml>"
    body, headers = callback(message)
    async with create_stateless_http_client() as client:
        adapter = WeComAdapter(client)
        result = await adapter.receive(channel(), credential(), body=body, headers=headers, now=NOW)
        assert result.message.text == "hello" and result.message.actor_id == "human" and result.message.group_id is None
        body, headers = callback(b"challenge", challenge=True)
        challenge = await adapter.receive(channel(), credential(), body=b"", headers=headers, now=NOW)
        assert challenge.reply.content_type == "text/plain" and challenge.reply.body == "challenge"
        body, headers = callback(message, corp="other")
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential(), body=body, headers=headers, now=NOW)
        body, headers = callback(message)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential(), body=body, headers={**headers, "msg_signature": "bad"}, now=NOW)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), credential(), body=body, headers=headers, now=NOW.replace(year=2027))


async def test_group_file_preserves_source_media_identity():
    raw = b"<xml><ToUserName>corp</ToUserName><FromUserName>human</FromUserName><MsgId>message</MsgId><AgentID>7</AgentID><ChatId>room</ChatId><MsgType>file</MsgType><MediaId>media</MediaId><Title>report.pdf</Title></xml>"
    body, headers = callback(raw)
    async with create_stateless_http_client() as client:
        result = await WeComAdapter(client).receive(channel(), credential(), body=body, headers=headers, now=NOW)
        assert result.message.group_id == "room" and result.message.attachments[0].external_id == "media"


@pytest.mark.parametrize("status,expected", [(200, "delivered"), (403, "failed"), (500, "uncertain")])
async def test_http_application_send_redacts_required_query_secrets(status, expected, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    observed = []
    def peer(request):
        observed.append(request.url)
        assert "cookie" not in request.headers and "x-client-default" not in request.headers
        if request.url.path.endswith("/gettoken"):
            assert b"corpsecret=corp-secret" in request.url.raw_path
            return httpx.Response(200, json={"errcode": 0, "access_token": "access-secret"})
        assert b"access_token=access-secret" in request.url.raw_path
        payload = json.loads(request.content)
        assert payload["agentid"] == 7 and "enable_duplicate_check" not in payload
        return httpx.Response(status, json={"errcode": 0, "msgid": "sent"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        client.headers["x-client-default"] = "bad"
        result = await WeComAdapter(client).send(channel(), credential(), destination="human", content=DeliveryContent("hello"), delivery_key="delivery")
        assert result.status == expected
    assert "corp-secret" not in caplog.text and "access-secret" not in caplog.text
    assert all("secret" not in str(url) and "secret" not in repr(url) for url in observed)


async def test_socket_authentication_reply_inside_callback_and_private_media_context():
    received = asyncio.Queue()
    closed, finished = asyncio.Event(), asyncio.Event()
    sent = []
    class Socket:
        async def recv(self):
            return await received.get()
        async def send(self, raw):
            frame = json.loads(raw)
            sent.append(frame)
            if frame["cmd"] == "aibot_subscribe":
                assert frame["body"] == {"bot_id": "bot", "secret": "bot-secret"}
                received.put_nowait(json.dumps({"headers": frame["headers"], "errcode": 0}))
                received.put_nowait(json.dumps({"cmd": "aibot_msg_callback", "headers": {"req_id": "callback"}, "body": {
                    "msgid": "message", "aibotid": "bot", "from": {"userid": "human"}, "chattype": "single", "msgtype": "image",
                    "image": {"url": "https://wework.qpic.cn/media?token=download-secret", "aeskey": KEY}}}))
            elif frame["cmd"] == "aibot_send_msg":
                received.put_nowait(json.dumps({"headers": frame["headers"], "errcode": 0}))
    @asynccontextmanager
    async def connector(url):
        assert url == "wss://openws.work.weixin.qq.com"
        try:
            yield Socket()
        finally:
            closed.set()
    config, secret = channel("websocket"), credential("websocket")
    accepted = []
    async with create_stateless_http_client() as client:
        adapter = WeComAdapter(client, connector=connector)
        async def consume(result):
            accepted.append(result)
            # The socket receiver must keep consuming acknowledgements during product callbacks.
            outcome = await adapter.send(config, secret, destination="human", content=DeliveryContent("ack"), delivery_key="reply")
            assert outcome.status == "delivered"
            finished.set()
        task = asyncio.create_task(adapter.listen(config, secret, consume))
        try:
            await asyncio.wait_for(finished.wait(), timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not adapter._connections and not adapter._starting
    assert closed.is_set() and len(accepted) == 1
    result = accepted[0]
    visible = json.dumps(asdict(result.message))
    assert "download-secret" not in visible and KEY not in visible
    assert result.private_context.media[0].aes_key.get_secret_value() == KEY
    assert result.message.attachments[0].external_id == result.private_context.media[0].reference_id
    assert not any(task.get_name() in {"wecom-channel-ping", "wecom-channel-input"} for task in asyncio.all_tasks())


async def test_missing_socket_heartbeat_ack_terminates_owned_tasks(monkeypatch):
    monkeypatch.setattr(wecom, "_HEARTBEAT_SECONDS", .01)
    queue, closed = asyncio.Queue(), asyncio.Event()
    class Socket:
        async def recv(self):
            return await queue.get()
        async def send(self, raw):
            frame = json.loads(raw)
            if frame["cmd"] == "aibot_subscribe":
                queue.put_nowait(json.dumps({"headers": frame["headers"], "errcode": 0}))
    @asynccontextmanager
    async def connector(url):
        try:
            yield Socket()
        finally:
            closed.set()
    async with create_stateless_http_client() as client:
        async def consume(result):
            pytest.fail("No event")
        from app.modules.channel.public import ListenerDisconnected
        with pytest.raises(ListenerDisconnected):
            await asyncio.wait_for(WeComAdapter(client, connector=connector).listen(channel("websocket"), credential("websocket"), consume), timeout=2)
    assert closed.is_set() and not any(task.get_name().startswith("wecom-channel") for task in asyncio.all_tasks())


async def test_download_decrypts_only_private_media_and_redacts_signed_url(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    padder = padding.PKCS7(256).padder()
    padded = padder.update(b"private file") + padder.finalize()
    encryptor = Cipher(algorithms.AES(b"a" * 32), modes.CBC(b"a" * 16)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    def peer(request):
        assert request.url.raw_path == b"/media?token=download-secret"
        return httpx.Response(200, content=ciphertext)
    private = MediaContext(reference_id="media", download_url=SecretStr("https://wework.qpic.cn/media?token=download-secret"),
        aes_key=SecretStr(KEY), name="file", media_type=None)
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        result = await WeComAdapter(client).download_media(private, maximum=100)
        assert result == b"private file"
    assert "download-secret" not in caplog.text and KEY not in caplog.text


async def test_customer_service_notice_bounded_sync_and_independent_send(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    body, headers = callback(b"<xml><ToUserName>corp</ToUserName><MsgType>event</MsgType><Event>kf_msg_or_event</Event><Token>sync-secret</Token><OpenKfId>kf</OpenKfId></xml>")
    notice = customer_service_notice(channel(), credential(), body=body, headers=headers, now=NOW)
    assert notice.open_kfid == "kf" and "sync-secret" not in repr(notice)
    def peer(request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "access-secret"})
        value = json.loads(request.content)
        if request.url.path.endswith("/sync_msg"):
            assert value["token"] == "sync-secret" and value["open_kfid"] == "kf" and value["limit"] == 20
            return httpx.Response(200, json={"errcode": 0, "has_more": 1, "next_cursor": "cursor-secret", "msg_list": [
                {"origin": 3, "msgtype": "text", "msgid": "kf-message", "open_kfid": "kf", "external_userid": "customer", "text": {"content": "question"}},
                {"origin": 5, "msgtype": "text", "msgid": "bot-message", "text": {"content": "do not self-trigger"}}]})
        assert request.url.path.endswith("/kf/send_msg") and value["touser"] == "customer"
        return httpx.Response(200, json={"errcode": 0, "msgid": "kf-reply"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as client:
        adapter = WeComAdapter(client)
        assert (await adapter.receive_customer_service_notice(channel(), credential(), body=body, headers=headers, now=NOW)).event_id == notice.event_id
        page = await adapter.sync_customer_service(channel(), credential(), open_kfid=notice.open_kfid, event_token=notice.token)
        assert len(page.messages) == 1 and page.messages[0].conversation_id == "kf:kf:customer"
        assert page.next_cursor.value == "cursor-secret" and "cursor-secret" not in repr(page)
        result = await adapter.send(channel(), credential(), destination="kf:kf:customer", content=DeliveryContent("answer"), delivery_key="delivery")
        assert result.status == "delivered"
    assert "access-secret" not in caplog.text and "sync-secret" not in caplog.text


async def test_native_websocket_client_and_server_exchange_then_close():
    closed, delivered = asyncio.Event(), asyncio.Event()
    async def peer(socket):
        try:
            auth = json.loads(await socket.recv())
            assert auth["cmd"] == "aibot_subscribe" and auth["body"]["bot_id"] == "bot"
            await socket.send(json.dumps({"headers": auth["headers"], "errcode": 0}))
            await socket.send(json.dumps({"cmd": "aibot_msg_callback", "headers": {"req_id": "incoming"},
                "body": {"aibotid": "bot", "msgid": "native-event", "from": {"userid": "human"},
                    "chattype": "single", "msgtype": "text", "text": {"content": "hello"}}}))
            async for raw in socket:
                frame = json.loads(raw)
                await socket.send(json.dumps({"headers": frame["headers"], "errcode": 0}))
        finally:
            closed.set()
    async with serve(peer, "127.0.0.1", 0, logger=wecom._WIRE_LOGGER) as server:
        port = server.sockets[0].getsockname()[1]
        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}", logger=wecom._WIRE_LOGGER) as socket:
                yield socket
        config, secret = channel("websocket"), credential("websocket")
        async with create_stateless_http_client() as client:
            adapter = WeComAdapter(client, connector=connector)
            async def consume(result):
                assert result.message.event_id == "native-event"
                assert (await adapter.send(config, secret, destination="human", content=DeliveryContent("reply"), delivery_key="native-send")).status == "delivered"
                delivered.set()
            task = asyncio.create_task(adapter.listen(config, secret, consume))
            try:
                await asyncio.wait_for(delivered.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(closed.wait(), 2)
            assert not adapter._connections


def test_wire_logger_does_not_inherit_root_debug_level(caplog):
    caplog.set_level(logging.DEBUG)
    assert not wecom._WIRE_LOGGER.isEnabledFor(logging.DEBUG)
