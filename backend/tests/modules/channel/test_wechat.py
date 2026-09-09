import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.wechat import WeChatAdapter, WeChatSessionExpired
from app.modules.channel.reply_context import ReplyContext
from app.modules.credential.public import Secret


def channel():
    tenant, agent = uuid4(), uuid4()
    return ChannelView(uuid4(), tenant, agent, "wechat", "bot@im.bot", uuid4(), True, "agent", agent,
        '{"connection_mode":"long_poll","base_url":"https://ilinkai.weixin.qq.com","channel_version":"1.0.0"}')


SECRET = Secret('{"version":1,"bot_token":"private-bot","route_tag":"route"}')


async def test_qr_enrollment_image_and_status_keep_tokens_private(caplog):
    paths = []
    def peer(request):
        paths.append(request.url.path)
        assert "qr-secret" not in str(request.url)
        if request.url.path.endswith("get_bot_qrcode"):
            assert request.method == "POST"
            assert json.loads(request.content) == {"local_token_list": []}
            return httpx.Response(200, json={"qrcode": "qr-secret", "qrcode_img_content": "https://liteapp.weixin.qq.com/image?ticket=qr-secret"})
        if request.url.path == "/image":
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        assert request.url.params["qrcode"] == "qr-secret"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"status": "confirmed", "bot_token": "new-private-token", "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "human", "baseurl": "https://ilinkai.weixin.qq.com"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = WeChatAdapter(http)
        challenge = await adapter.create_qr()
        assert "qr-secret" not in repr(challenge)
        assert await adapter.qr_image(challenge.image_url) == (b"png", "image/png")
        status = await adapter.qr_status(challenge.qrcode)
        assert status.bot_token.get_secret_value() == "new-private-token"
        assert "new-private-token" not in repr(status)
    assert len(paths) == 3 and "qr-secret" not in caplog.text


async def test_qr_html_is_not_proxied_and_untrusted_hosts_are_rejected():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<script>unsafe</script>"))) as http:
        adapter = WeChatAdapter(http)
        url = "https://weixin.qq.com/login?ticket=secret"
        assert await adapter.qr_image(SecretStr(url)) == (url.encode(), "text/plain")
        with pytest.raises(InvalidInput):
            await adapter.qr_image(SecretStr("https://evil.invalid/qr"))


def inbound(*, kind=1, target="bot@im.bot"):
    return {"message_id": 123, "from_user_id": "user", "to_user_id": target,
        "message_type": kind, "context_token": "private-context", "item_list": [{"type": 1, "text_item": {"text": "hello"}}]}


async def test_poll_is_authenticated_cursor_bounded_and_context_is_not_in_message_text():
    def peer(request):
        assert request.headers["authorization"] == "Bearer private-bot"
        assert request.headers["skrouteTag"] == "route"
        assert json.loads(request.content)["get_updates_buf"] == "cursor"
        assert "X-WECHAT-UIN" in request.headers
        return httpx.Response(200, json={"ret": 0, "msgs": [inbound(), inbound(kind=2)], "get_updates_buf": "next"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        batch = await WeChatAdapter(http).poll_once(channel(), SECRET, cursor=SecretStr("cursor"), now=datetime.now(UTC))
        assert len(batch.messages) == 1 and batch.next_cursor.get_secret_value() == "next"
        event = batch.messages[0]
        assert event.message.text == "hello" and event.message.conversation_id == "user"
        assert event.private_context.reply_token.get_secret_value() == "private-context"
        assert "private-context" not in repr(event.message) and "private-context" not in repr(event)


async def test_session_expiry_exits_listener_and_releases_ownership():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ret": -14}))) as http:
        adapter = WeChatAdapter(http)
        with pytest.raises(WeChatSessionExpired):
            await adapter.listen(channel(), SECRET, lambda _: pytest.fail("Expired session must not deliver"))
        assert not adapter._listening and not http.is_closed


async def test_listener_advances_after_acceptance_then_cancellation_closes_poll():
    second = asyncio.Event()
    closed = asyncio.Event()
    polls = []
    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            second.set()
            await asyncio.Future()
            yield b""
        async def aclose(self):
            closed.set()
    def peer(request):
        polls.append(json.loads(request.content)["get_updates_buf"])
        if len(polls) == 1:
            return httpx.Response(200, json={"msgs": [inbound()], "get_updates_buf": "next"})
        return httpx.Response(200, stream=Blocked())
    seen = []
    async def accept(message):
        seen.append(message)
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = WeChatAdapter(http)
        task = asyncio.create_task(adapter.listen(channel(), SECRET, accept))
        await asyncio.wait_for(second.wait(), 1)
        assert polls == ["", "next"] and len(seen) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and not adapter._listening


async def test_text_chunks_keep_exact_text_stable_client_ids_and_private_context():
    sent = []
    def peer(request):
        msg = json.loads(request.content)["msg"]
        assert msg["context_token"] == "private-context"
        assert msg["to_user_id"] == "user" and msg["message_type"] == 2
        sent.append(msg)
        return httpx.Response(200, json={"ret": 0})
    text = " a\n" * 1500
    context = ReplyContext(provider="wechat", conversation_id="user", reply_token=SecretStr("private-context"))
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = WeChatAdapter(http)
        current = channel()
        assert (await adapter.send(current, SECRET, destination="user", content=DeliveryContent(text), delivery_key="key", reply_context=context)).status == "delivered"
        assert "".join(value["item_list"][0]["text_item"]["text"] for value in sent) == text
        assert all(len(value["item_list"][0]["text_item"]["text"]) <= 2000 for value in sent)
        ids = [value["client_id"] for value in sent]
        sent.clear()
        await adapter.send(current, SECRET, destination="user", content=DeliveryContent(text), delivery_key="key", reply_context=context)
        assert [value["client_id"] for value in sent] == ids


async def test_partial_multichunk_failure_is_uncertain_not_safe_retry():
    calls = 0
    def peer(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ret": 0}) if calls == 1 else httpx.Response(400)
    context = ReplyContext(provider="wechat", conversation_id="user", reply_token=SecretStr("private-context"))
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await WeChatAdapter(http).send(channel(), SECRET, destination="user", content=DeliveryContent("x" * 4001), delivery_key="key", reply_context=context)
        assert result.status == "uncertain" and calls == 2


async def test_wrong_bot_and_unsigned_http_are_denied():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"msgs": [inbound(target="other")] }))) as http:
        adapter = WeChatAdapter(http)
        with pytest.raises(AccessDenied):
            await adapter.poll_once(channel(), SECRET, cursor=SecretStr(""), now=datetime.now(UTC))
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), SECRET, body=b"{}", headers={}, now=datetime.now(UTC))


async def test_media_only_item_does_not_poison_following_supported_text(caplog):
    media = inbound()
    media["item_list"] = [{"type": 2, "image_item": {"media": {"aes_key": "private-media-key"}}}]
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            json={"msgs": [media, inbound()], "get_updates_buf": "advanced"}))) as http:
        result = await WeChatAdapter(http).poll_once(channel(), SECRET, cursor=SecretStr(""), now=datetime.now(UTC))
    assert len(result.messages) == 1 and result.next_cursor.get_secret_value() == "advanced"
    assert "not accepted" in caplog.text and "private-media-key" not in caplog.text
