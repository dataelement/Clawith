"""DingTalk Stream intake and native bot text/media transport."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, Protocol, cast
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import ValidationError
from websockets.asyncio.client import connect

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
from app.modules.channel.adapters import _json, _text
from app.modules.channel.contracts import (
    AttachmentReference,
    ChannelView,
    DeliveryContent,
    InboundResult,
    IncomingMessage,
    Provider,
    SendOutcome,
)
from app.modules.channel.reply_context import ReplyContext
from app.modules.channel.settings import DingTalkSettings
from app.modules.channel.transport import protect_request_url
from app.modules.credential.public import Secret

_API = "https://api.dingtalk.com/v1.0"
_TOPIC = "/v1.0/im/bot/messages/get"
_WIRE_LOGGER = logging.Logger("clawith.channel.dingtalk.wire", level=logging.WARNING)  # noqa: LOG001 -- websocket URI contains a connection ticket; never inherit global DEBUG.


class _Socket(Protocol):
    async def recv(self) -> str | bytes: ...
    async def send(self, data: str | bytes) -> None: ...


Connector = Callable[[str], AbstractAsyncContextManager[_Socket]]


def _connect(url: str):
    return cast(AbstractAsyncContextManager[_Socket], connect(url, max_size=262144, max_queue=16, ping_interval=30, close_timeout=5, logger=_WIRE_LOGGER))


class _Rejected(InvalidInput):
    pass


def _configuration(channel: ChannelView, credential: Secret):
    if channel.provider != "dingtalk" or not channel.enabled:
        raise AccessDenied("DingTalk Channel is unavailable")
    try:
        settings = DingTalkSettings.model_validate_json(channel.settings_json)
    except ValidationError:
        raise InvalidInput("DingTalk settings are invalid") from None
    data = _json(credential.value)
    if set(data) != {"version", "app_secret"} or type(data["version"]) is not int or data["version"] != 1:
        raise InvalidInput("DingTalk Credential bundle is invalid")
    return settings, _text(data["app_secret"], maximum=8192)


def _message(channel: ChannelView, data: dict) -> IncomingMessage:
    if data.get("robotCode") is not None and data["robotCode"] != DingTalkSettings.model_validate_json(channel.settings_json).robot_code:
        raise AccessDenied("DingTalk message belongs to another robot")
    actor = _text(data.get("senderStaffId"), maximum=500)
    group = data.get("conversationType") == "2"
    if data.get("conversationType") not in ("1", "2"):
        raise InvalidInput("DingTalk conversation type is unsupported")
    conversation = _text(data.get("conversationId"), maximum=500) if group else actor
    content = data.get("content") or {}
    if not isinstance(content, dict):
        raise InvalidInput("DingTalk content is invalid")
    kind = data.get("msgtype")
    text = ""
    attachments = []
    if kind == "text":
        value = data.get("text")
        if not isinstance(value, dict):
            raise InvalidInput("DingTalk text is invalid")
        text = _text(value.get("content"), maximum=262144)
    elif kind in ("picture", "file", "video", "audio"):
        code = _text(content.get("downloadCode") or data.get("downloadCode"), maximum=512)
        name = _text(content.get("fileName") or kind, maximum=512)
        attachments.append(AttachmentReference(code, name, None))
        if kind == "audio" and content.get("recognition"):
            text = _text(content["recognition"], maximum=262144)
    elif kind == "richText":
        parts = content.get("richText")
        if not isinstance(parts, list) or len(parts) > 64:
            raise InvalidInput("DingTalk rich text exceeds its bound")
        for part in parts:
            values = part if isinstance(part, list) else [part]
            if len(values) > 64:
                raise InvalidInput("DingTalk rich text exceeds its bound")
            for item in values:
                if not isinstance(item, dict):
                    raise InvalidInput("DingTalk rich text is invalid")
                if "text" in item:
                    text += _text(item["text"], maximum=262144)
                elif "downloadCode" in item:
                    attachments.append(AttachmentReference(_text(item["downloadCode"], maximum=512), "image", None))
    else:
        raise InvalidInput("DingTalk message kind is unsupported")
    if len(text.encode()) > 262144 or len(attachments) > 64:
        raise InvalidInput("DingTalk message exceeds its bound")
    return IncomingMessage(_text(data.get("msgId"), maximum=512), actor,
        ("group:" if group else "user:") + conversation, conversation if group else None, text, None, tuple(attachments))


class DingTalkAdapter:
    provider: Provider = "dingtalk"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10, connector: Connector = _connect) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("DingTalk HTTP deadline is invalid")
        self.http, self.timeout, self.connector = http, timeout_seconds, connector
        self._listening: set[UUID] = set()

    async def _request(self, method: str, url: str, *, headers=None, payload=None, files=None, data=None, maximum=262144):
        require_stateless_http_client(self.http)
        request = protect_request_url(httpx.Request(method, url, headers=headers, json=payload, files=files, data=data,
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()}))
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if 400 <= response.status_code < 500:
                    raise _Rejected("DingTalk request was rejected")
                if response.status_code >= 300:
                    raise InvalidInput("DingTalk request was not confirmed")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > maximum:
                        raise InvalidInput("DingTalk response exceeds its bound")
                    body.extend(chunk)
                return _json(bytes(body))
            finally:
                await response.aclose()

    async def _token(self, channel: ChannelView, credential: Secret) -> str:
        _, secret = _configuration(channel, credential)
        result = await self._request("POST", _API + "/oauth2/accessToken",
            payload={"appKey": channel.external_identity, "appSecret": secret})
        return _text(result.get("accessToken"), maximum=8192)

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes, headers: dict[str, str], now: datetime) -> InboundResult:
        raise AccessDenied("DingTalk messages are accepted only from the authenticated Stream connection")

    async def listen(self, channel: ChannelView, credential: Secret, on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        from app.modules.channel.transport import listen_transport
        await listen_transport(self._listen(channel, credential, on_message))

    async def _listen(self, channel: ChannelView, credential: Secret, on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        _settings, secret = _configuration(channel, credential)
        if channel.id in self._listening or len(self._listening) >= 100:
            raise InvalidInput("DingTalk connection is already owned or capacity is full")
        self._listening.add(channel.id)
        try:
            opened = await self._request("POST", _API + "/gateway/connections/open", payload={
                "clientId": channel.external_identity, "clientSecret": secret,
                "subscriptions": [{"type": "CALLBACK", "topic": _TOPIC}], "ua": "clawith-native/1", "localIp": ""})
            endpoint = _text(opened.get("endpoint"), maximum=4096)
            parsed = urlsplit(endpoint)
            if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise InvalidInput("DingTalk Stream endpoint is invalid")
            ticket = _text(opened.get("ticket"), maximum=8192)
            async with self.connector(endpoint + "?ticket=" + quote(ticket, safe="")) as socket:
                while True:
                    frame = _json(await socket.recv())
                    headers = frame.get("headers")
                    if not isinstance(headers, dict):
                        raise InvalidInput("DingTalk Stream headers are invalid")
                    message_id = _text(headers.get("messageId"), maximum=512)
                    kind, topic = frame.get("type"), headers.get("topic")
                    if kind == "SYSTEM":
                        await socket.send(json.dumps({"code": 200, "headers": {"messageId": message_id, "contentType": "application/json"},
                            "message": "OK", "data": frame.get("data", "{}")}))
                        if topic == "disconnect":
                            return
                        continue
                    if kind != "CALLBACK" or topic != _TOPIC:
                        raise InvalidInput("DingTalk Stream topic is unsupported")
                    data = _json(frame.get("data", ""))
                    await on_message(InboundResult(message=_message(channel, data)))
                    # Commit acceptance before acknowledging; failed intake receives no success ACK.
                    await socket.send(json.dumps({"code": 200, "headers": {"messageId": message_id, "contentType": "application/json"},
                        "message": "OK", "data": '{"response":"OK"}'}))
        finally:
            self._listening.discard(channel.id)

    async def download_media(self, channel: ChannelView, credential: Secret, reference: str, *, max_bytes: int = 20 * 1024 * 1024) -> tuple[bytes, str | None]:
        settings, _ = _configuration(channel, credential)
        if not 0 < max_bytes <= 20 * 1024 * 1024:
            raise InvalidInput("DingTalk download bound is invalid")
        token = await self._token(channel, credential)
        coordinates = await self._request("POST", _API + "/robot/messageFiles/download",
            headers={"x-acs-dingtalk-access-token": token}, payload={"downloadCode": _text(reference, maximum=512), "robotCode": settings.robot_code})
        url = _text(coordinates.get("downloadUrl"), maximum=8192)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise InvalidInput("DingTalk media coordinate is invalid")
        request = protect_request_url(httpx.Request("GET", url, extensions={"timeout": httpx.Timeout(self.timeout).as_dict()}))
        require_stateless_http_client(self.http)
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("DingTalk media download was not confirmed")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result) + len(chunk) > max_bytes:
                        raise InvalidInput("DingTalk media exceeds its bound")
                    result.extend(chunk)
                return bytes(result), response.headers.get("content-type")
            finally:
                await response.aclose()

    async def upload_file(self, channel: ChannelView, credential: Secret, *, filename: str, content: bytes, image: bool = False) -> str:
        if not content or len(content) > 20 * 1024 * 1024:
            raise InvalidInput("DingTalk upload exceeds its bound")
        token = await self._token(channel, credential)
        url = "https://oapi.dingtalk.com/media/upload?access_token=" + quote(token, safe="") + "&type=" + ("image" if image else "file")
        result = await self._request("POST", url, files={"media": (_text(filename, maximum=512), content)})
        if result.get("errcode") != 0:
            raise InvalidInput("DingTalk upload was rejected")
        return _text(result.get("media_id"), maximum=512)

    async def _send(self, channel: ChannelView, credential: Secret, destination: str, key: str, parameter: dict) -> SendOutcome:
        try:
            settings, _ = _configuration(channel, credential)
            mode, target = destination.split(":", 1)
            if mode not in ("user", "group"):
                raise InvalidInput("DingTalk destination is invalid")
            _text(target, maximum=500)
            token = await self._token(channel, credential)
        except (ValueError, InvalidInput, AccessDenied, httpx.HTTPError, TimeoutError):
            return SendOutcome("failed", error="dingtalk_configuration_or_token_unavailable")
        body: dict[str, object] = {"robotCode": settings.robot_code, "msgKey": key, "msgParam": json.dumps(parameter, ensure_ascii=False)}
        body.update({"userIds": [target]} if mode == "user" else {"openConversationId": target})
        try:
            result = await self._request("POST", _API + ("/robot/oToMessages/batchSend" if mode == "user" else "/robot/groupMessages/send"),
                headers={"x-acs-dingtalk-access-token": token}, payload=body)
            acknowledgement = _text(result.get("processQueryKey"), maximum=512)
            return SendOutcome("delivered", acknowledgement=acknowledgement)
        except _Rejected:
            return SendOutcome("failed", error="dingtalk_send_rejected")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="dingtalk_send_not_confirmed")

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str, content: DeliveryContent,
                   delivery_key: str, reply_context: ReplyContext | None = None, reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if content.attachments or not content.text or len(content.text.encode()) > 20000:
            return SendOutcome("failed", error="dingtalk_message_requires_bounded_text_or_native_media")
        return await self._send(channel, credential, destination, "sampleText", {"content": content.text})

    async def send_file(self, channel: ChannelView, credential: Secret, *, destination: str, media_id: str,
                        filename: str, image: bool = False) -> SendOutcome:
        _text(media_id, maximum=512)
        _text(filename, maximum=512)
        return await self._send(channel, credential, destination, "sampleImageMsg" if image else "sampleFile",
            {"photoURL": media_id} if image else {"mediaId": media_id, "fileName": filename, "fileType": filename.rsplit(".", 1)[-1]})
