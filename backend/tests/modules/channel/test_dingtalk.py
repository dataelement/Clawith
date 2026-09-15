import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.contracts import ChannelView, DeliveryContent
from app.modules.channel.providers.dingtalk import DingTalkAdapter
from app.modules.credential.public import Secret


def channel():
    tenant, agent = uuid4(), uuid4()
    return ChannelView(uuid4(), tenant, agent, "dingtalk", "app-key", uuid4(), True, "agent", agent,
        '{"connection_mode":"stream","robot_code":"robot"}')


SECRET = Secret('{"version":1,"app_secret":"private-app-secret"}')


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def recv(self):
        return await self.incoming.get()

    async def send(self, message):
        self.sent.append(json.loads(message))


def frame(kind="text", group=False):
    content = {"msgId": "event", "senderStaffId": "staff", "robotCode": "robot",
        "conversationId": "cid", "conversationType": "2" if group else "1", "msgtype": kind,
        "text": {"content": "hello"}, "content": {"downloadCode": "download", "fileName": "report.pdf"}}
    return json.dumps({"type": "CALLBACK", "headers": {"messageId": "frame", "topic": "/v1.0/im/bot/messages/get"}, "data": json.dumps(content)})


@pytest.mark.parametrize("kind,group", [("text", False), ("picture", False), ("file", True)])
async def test_stream_authenticates_commits_then_acks_and_cancels_owned_socket(kind, group):
    socket = Socket()
    received = asyncio.Event()
    continue_acceptance = asyncio.Event()
    observations = []
    @asynccontextmanager
    async def connector(url):
        assert url == "wss://stream.dingtalk.com/path?ticket=private-ticket"
        try:
            yield socket
        finally:
            socket.closed = True
    def peer(request):
        assert request.url.path == "/v1.0/gateway/connections/open"
        assert json.loads(request.content)["clientSecret"] == "private-app-secret"
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"endpoint": "wss://stream.dingtalk.com/path", "ticket": "private-ticket"})
    async def accept(value):
        observations.append(value)
        received.set()
        await continue_acceptance.wait()
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = DingTalkAdapter(http, connector=connector)
        task = asyncio.create_task(adapter.listen(channel(), SECRET, accept))
        await socket.incoming.put(frame(kind, group))
        await asyncio.wait_for(received.wait(), 1)
        assert socket.sent == []
        continue_acceptance.set()
        async with asyncio.timeout(1):
            while not socket.sent:
                await asyncio.sleep(0)
        assert socket.sent[0]["code"] == 200
        value = observations[0].message
        assert value.actor_id == "staff" and value.group_id == ("cid" if group else None)
        assert value.conversation_id == ("group:cid" if group else "user:staff")
        if kind != "text":
            assert value.attachments[0].external_id == "download"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert socket.closed and not adapter._listening and not http.is_closed


@pytest.mark.parametrize("destination,path", [("user:staff", "/v1.0/robot/oToMessages/batchSend"), ("group:cid", "/v1.0/robot/groupMessages/send")])
async def test_native_text_delivery_and_failure_uncertainty(destination, path):
    requests = []
    def peer(request):
        requests.append(request)
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "access-secret"})
        assert request.url.path == path
        assert request.headers["x-acs-dingtalk-access-token"] == "access-secret"
        body = json.loads(request.content)
        assert body["robotCode"] == "robot" and body["msgKey"] == "sampleText"
        return httpx.Response(200, json={"processQueryKey": "ack"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await DingTalkAdapter(http).send(channel(), SECRET, destination=destination, content=DeliveryContent("hello"), delivery_key="delivery")
        assert result.status == "delivered" and result.acknowledgement == "ack" and len(requests) == 2


async def test_native_media_download_upload_and_send_use_private_url_and_handles(caplog):
    paths = []
    def peer(request):
        paths.append(request.url.path)
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "secret-token"})
        if request.url.path.endswith("/download"):
            assert json.loads(request.content) == {"downloadCode": "handle", "robotCode": "robot"}
            return httpx.Response(200, json={"downloadUrl": "https://cdn.dingtalk.com/private-media?token=private-url"})
        if request.url.path == "/private-media":
            assert "private-url" not in str(request.url)
            return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/png"})
        if request.url.path == "/media/upload":
            assert request.url.params["access_token"] == "secret-token"
            assert b"image-bytes" in request.content
            return httpx.Response(200, json={"errcode": 0, "media_id": "uploaded"})
        assert json.loads(request.content)["msgKey"] == "sampleImageMsg"
        return httpx.Response(200, json={"processQueryKey": "sent"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = DingTalkAdapter(http)
        content, media = await adapter.download_media(channel(), SECRET, "handle")
        assert content == b"image-bytes" and media == "image/png"
        uploaded = await adapter.upload_file(channel(), SECRET, filename="image.png", content=content, image=True)
        assert uploaded == "uploaded"
        assert (await adapter.send_file(channel(), SECRET, destination="group:cid", media_id=uploaded, filename="image.png", image=True)).status == "delivered"


async def test_unsigned_http_ingress_and_wrong_channel_fail_before_network():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as http:
        adapter = DingTalkAdapter(http)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), SECRET, body=b"{}", headers={}, now=None)
        result = await adapter.send(replace(channel(), enabled=False), SECRET, destination="user:x", content=DeliveryContent("hi"), delivery_key="d")
        assert result.status == "failed"


@pytest.mark.parametrize("status,expected", [(400, "failed"), (500, "uncertain")])
async def test_send_rejection_differs_from_ambiguous_effect(status, expected):
    def peer(request):
        return httpx.Response(200, json={"accessToken": "token"}) if request.url.path.endswith("accessToken") else httpx.Response(status)
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await DingTalkAdapter(http).send(channel(), SECRET, destination="user:x", content=DeliveryContent("hi"), delivery_key="d")
        assert result.status == expected


async def test_download_bound_and_credential_version_fail():
    def peer(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        if request.url.path.endswith("download"):
            return httpx.Response(200, json={"downloadUrl": "https://cdn.dingtalk.com/file"})
        return httpx.Response(200, content=b"oversized")
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = DingTalkAdapter(http)
        with pytest.raises(InvalidInput):
            await adapter.download_media(channel(), SECRET, "handle", max_bytes=1)
        assert (await adapter.send(channel(), Secret('{"version":2,"app_secret":"x"}'), destination="user:x", content=DeliveryContent("hi"), delivery_key="d")).status == "failed"
