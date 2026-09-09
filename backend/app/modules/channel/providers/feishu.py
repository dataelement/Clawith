"""Feishu wire transport, isolated from product input and Run ownership."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import ValidationError
from websockets.asyncio.client import connect

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
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
from app.modules.channel.settings import FeishuSettings
from app.modules.credential.public import Secret

_MAX = 256 * 1024
_API = "https://open.feishu.cn"
_WIRE_LOGGER = logging.Logger("clawith.channel.feishu.wire", level=logging.WARNING)  # noqa: LOG001 -- isolated wire logger must not inherit DEBUG and reveal authentication coordinates.


class _HTTPRejected(InvalidInput):
    pass


class _Socket(Protocol):
    async def recv(self) -> str | bytes: ...
    async def send(self, data: str | bytes) -> None: ...


Connector = Callable[[str], AbstractAsyncContextManager[_Socket]]


def _connect(url: str) -> AbstractAsyncContextManager[_Socket]:
    return cast(AbstractAsyncContextManager[_Socket], connect(url, max_size=_MAX, max_queue=16,
        open_timeout=10, close_timeout=5, ping_interval=None, logger=_WIRE_LOGGER, proxy=None))


def _text(value: object, *, maximum: int = 512, empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not empty) or len(value.encode()) > maximum:
        raise InvalidInput("Feishu field exceeds its supported shape or bound")
    return value


def _json(raw: str | bytes) -> dict[str, Any]:
    # Provider JSON is untyped; only explicitly validated fields cross the adapter boundary.
    if len(raw.encode() if isinstance(raw, str) else raw) > _MAX:
        raise InvalidInput("Feishu payload exceeds its byte bound")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("Feishu payload is invalid") from None
    if not isinstance(value, dict):
        raise InvalidInput("Feishu payload must be an object")
    return value


def _secrets(credential: Secret) -> dict[str, Any]:
    value = _json(credential.value)
    if set(value) != {"version", "app_id", "app_secret", "verification_token", "encrypt_key"} or type(value["version"]) is not int or value["version"] != 1:
        raise InvalidInput("Feishu Credential bundle is unsupported")
    for name in ("app_id", "app_secret", "verification_token", "encrypt_key"):
        _text(value[name], maximum=8192, empty=name in ("encrypt_key", "verification_token"))
    return value


def _settings(channel: ChannelView, secrets: dict[str, Any]) -> FeishuSettings:
    if channel.provider != "feishu" or not channel.enabled or channel.external_identity != secrets["app_id"]:
        raise AccessDenied("Feishu channel identity is unavailable")
    try:
        settings = FeishuSettings.model_validate_json(channel.settings_json)
        if settings.connection_mode == "webhook" and not secrets["verification_token"]:
            raise InvalidInput("Feishu webhook verification token is required")
        return settings
    except ValidationError:
        raise InvalidInput("Feishu settings are invalid") from None


def _decrypt(encrypted: str, key: str) -> bytes:
    try:
        raw = base64.b64decode(encrypted, validate=True)
        if len(raw) < 32 or len(raw) % 16:
            raise ValueError
        decryptor = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(raw[:16])).decryptor()
        plain = decryptor.update(raw[16:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(plain) + unpadder.finalize()
    except ValueError:
        raise AccessDenied("Feishu encrypted callback is invalid") from None


def _normalize(channel: ChannelView, settings: FeishuSettings, payload: dict[str, Any]) -> InboundResult:
    header = payload.get("header")
    if not isinstance(header, dict) or header.get("app_id") != channel.external_identity or header.get("tenant_key") != settings.tenant_key:
        raise AccessDenied("Feishu event belongs to another app or Tenant")
    if header.get("event_type") != "im.message.receive_v1":
        return InboundResult()
    event = payload.get("event")
    if not isinstance(event, dict) or not isinstance(event.get("sender"), dict) or not isinstance(event.get("message"), dict):
        raise InvalidInput("Feishu message event is invalid")
    sender, message = event["sender"], event["message"]
    if sender.get("sender_type") != "user":
        return InboundResult()
    identity = sender.get("sender_id")
    if not isinstance(identity, dict):
        raise InvalidInput("Feishu sender identity is invalid")
    actor = _text(identity.get("open_id"))
    conversation = _text(message.get("chat_id"))
    chat_type = message.get("chat_type")
    if chat_type not in ("p2p", "group"):
        raise InvalidInput("Feishu chat type is unsupported")
    mentions = message.get("mentions", [])
    if not isinstance(mentions, list) or len(mentions) > 100:
        raise InvalidInput("Feishu mentions exceed their bound")
    if chat_type == "group" and not any(isinstance(item, dict) and isinstance(item.get("id"), dict)
            and item["id"].get("open_id") == settings.bot_open_id for item in mentions):
        return InboundResult()
    message_id = _text(message.get("message_id"))
    content = _json(_text(message.get("content"), maximum=_MAX))
    kind = message.get("message_type")
    attachments: list[AttachmentReference] = []
    text = ""
    if kind == "text":
        text = _text(content.get("text", ""), maximum=_MAX, empty=True)
    elif kind in ("image", "file", "audio", "media", "sticker"):
        key = _text(content.get("image_key" if kind in ("image", "sticker") else "file_key"))
        name = _text(content.get("file_name", kind))
        attachments.append(AttachmentReference(f"{message_id}/{key}", name, None))
    elif kind == "post":
        if "content" not in content:
            localized = [value for value in content.values() if isinstance(value, dict) and "content" in value]
            if not localized:
                raise InvalidInput("Feishu localized post is invalid")
            content = localized[0]
        blocks = content.get("content")
        if not isinstance(blocks, list) or len(blocks) > 100:
            raise InvalidInput("Feishu post exceeds its supported bound")
        fragments = [_text(content.get("title", ""), maximum=_MAX, empty=True)]
        for row in blocks:
            if not isinstance(row, list) or len(row) > 100:
                raise InvalidInput("Feishu post row is invalid")
            for item in row:
                if not isinstance(item, dict):
                    raise InvalidInput("Feishu post element is invalid")
                if item.get("tag") == "text":
                    fragments.append(_text(item.get("text", ""), maximum=_MAX, empty=True))
                elif item.get("tag") == "a":
                    label = _text(item.get("text", ""), maximum=_MAX, empty=True)
                    href = _text(item.get("href", ""), maximum=8192, empty=True)
                    fragments.append(f"{label} ({href})" if href else label)
                elif item.get("tag") == "at":
                    label = _text(item.get("user_name") or item.get("name") or "", maximum=512, empty=True)
                    if label:
                        fragments.append(f"@{label}")
                elif item.get("tag") == "img":
                    attachments.append(AttachmentReference(f"{message_id}/{_text(item.get('image_key'))}", "image", None))
        text = "\n".join(fragments)
    else:
        raise InvalidInput("Feishu message content type is unsupported")
    for mention in mentions:
        if not isinstance(mention, dict):
            raise InvalidInput("Feishu mention is invalid")
        placeholder = _text(mention.get("key"))
        label = _text(mention.get("name", ""), maximum=512, empty=True)
        text = text.replace(placeholder, f"@{label}" if label else "")
    if len(attachments) > 64 or len(text.encode()) > _MAX:
        raise InvalidInput("Feishu normalized message exceeds its bound")
    normalized = IncomingMessage(_text(header.get("event_id")), actor, conversation,
        conversation if chat_type == "group" else None, text,
        _text(message["parent_id"]) if message.get("parent_id") else None, tuple(attachments))
    if len(json.dumps(asdict(normalized), ensure_ascii=False).encode()) > _MAX:
        raise InvalidInput("Feishu normalized event exceeds its complete byte bound")
    return InboundResult(message=normalized)


class FeishuAdapter:
    provider: Provider = "feishu"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10, connector: Connector = _connect) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("Feishu HTTP deadline is invalid")
        self.http, self.timeout, self.connector = http, timeout_seconds, connector

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> InboundResult:
        secrets = _secrets(credential)
        settings = _settings(channel, secrets)
        if settings.connection_mode != "webhook" or now.tzinfo is None:
            raise AccessDenied("Feishu webhook is unavailable")
        envelope = _json(body)
        if "encrypt" in envelope:
            if not secrets["encrypt_key"]:
                raise AccessDenied("Feishu encryption is not configured")
            envelope = _json(_decrypt(_text(envelope["encrypt"], maximum=_MAX), secrets["encrypt_key"]))
        header = envelope.get("header", {})
        token = header.get("token") if isinstance(header, dict) and header.get("token") is not None else envelope.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, secrets["verification_token"]):
            raise AccessDenied("Feishu verification token is invalid")
        # Feishu's URL verification uses the decrypted token/challenge without event-signature headers.
        if envelope.get("type") == "url_verification":
            return InboundResult(challenge=_text(envelope.get("challenge"), maximum=4096))
        if secrets["encrypt_key"]:
            normalized = {key.lower(): value for key, value in headers.items()}
            stamp, nonce = normalized.get("x-lark-request-timestamp", ""), normalized.get("x-lark-request-nonce", "")
            if not stamp.isdigit() or len(stamp) > 16 or abs(now.timestamp() - int(stamp)) > 300 or not nonce or len(nonce) > 512:
                raise AccessDenied("Feishu callback timestamp or nonce is invalid")
            signature = hashlib.sha256((stamp + nonce + secrets["encrypt_key"]).encode() + body).hexdigest()
            if not hmac.compare_digest(signature, normalized.get("x-lark-signature", "")):
                raise AccessDenied("Feishu callback signature is invalid")
        return _normalize(channel, settings, envelope)

    async def _request(self, request: httpx.Request) -> dict[str, Any]:
        require_stateless_http_client(self.http)
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    if response.status_code in (400, 401, 403, 404, 422, 429):
                        raise _HTTPRejected("Feishu HTTP operation was rejected")
                    raise InvalidInput("Feishu HTTP operation was rejected")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > _MAX:
                        raise InvalidInput("Feishu response exceeds its byte bound")
                    raw.extend(chunk)
                return _json(bytes(raw))
            finally:
                await response.aclose()

    def _post(self, path: str, payload: object, token: str | None = None) -> httpx.Request:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        return httpx.Request("POST", _API + path, headers=headers, json=payload,
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})

    async def _token(self, secrets: dict[str, Any]) -> str:
        data = await self._request(self._post("/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": secrets["app_id"], "app_secret": secrets["app_secret"]}))
        if type(data.get("code")) is not int or data["code"] != 0:
            raise InvalidInput("Feishu token was not accepted")
        return _text(data.get("tenant_access_token"), maximum=8192)

    async def download_resource(self, channel: ChannelView, credential: Secret, *, message_id: str,
            resource_key: str, resource_type: str, maximum: int = 16 * 1024 * 1024) -> tuple[bytes, str | None]:
        """Caller authorizes the accepted message/resource association before requesting bytes."""
        secrets = _secrets(credential)
        _settings(channel, secrets)
        _text(message_id)
        _text(resource_key)
        if resource_type not in ("image", "file") or type(maximum) is not int or not 1 <= maximum <= 16 * 1024 * 1024:
            raise InvalidInput("Feishu resource request is invalid")
        token = await self._token(secrets)
        request = httpx.Request("GET", _API + f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(resource_key, safe='')}",
            params={"type": resource_type}, headers={"Authorization": "Bearer " + token},
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
        require_stateless_http_client(self.http)
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("Feishu resource download was rejected")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > maximum:
                        raise InvalidInput("Feishu resource exceeds the download bound")
                    content.extend(chunk)
                return bytes(content), response.headers.get("content-type")
            finally:
                await response.aclose()

    async def upload_file(self, channel: ChannelView, credential: Secret, *, filename: str, content: bytes) -> str:
        """Upload caller-authorized bytes; returning a file key is not message delivery."""
        secrets = _secrets(credential)
        _settings(channel, secrets)
        _text(filename, maximum=512)
        if not content or len(content) > 16 * 1024 * 1024:
            raise InvalidInput("Feishu upload exceeds its byte bound")
        token = await self._token(secrets)
        request = httpx.Request("POST", _API + "/open-apis/im/v1/files", headers={"Authorization": "Bearer " + token},
            data={"file_type": "stream", "file_name": filename}, files={"file": (filename, content, "application/octet-stream")},
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
        result = await self._request(request)
        if type(result.get("code")) is not int or result["code"] != 0 or not isinstance(result.get("data"), dict):
            raise InvalidInput("Feishu file upload was rejected")
        return _text(result["data"].get("file_key"))

    async def send_file(self, channel: ChannelView, credential: Secret, *, destination: str,
            file_key: str, delivery_key: str) -> SendOutcome:
        """Send one uploaded native handle under an independently owned delivery receipt."""
        try:
            secrets = _secrets(credential)
            _settings(channel, secrets)
            _text(destination)
            _text(file_key)
            _text(delivery_key)
            token = await self._token(secrets)
        except (InvalidInput, AccessDenied, httpx.HTTPError, TimeoutError):
            return SendOutcome("failed", error="feishu_configuration_or_authentication_failed")
        try:
            data = await self._request(self._post("/open-apis/im/v1/messages?receive_id_type=chat_id",
                {"receive_id": destination, "msg_type": "file", "content": json.dumps({"file_key": file_key}),
                    "uuid": hashlib.sha256(delivery_key.encode()).hexdigest()[:32]}, token))
            if type(data.get("code")) is not int:
                return SendOutcome("uncertain", error="feishu_acknowledgement_invalid")
            if data["code"] != 0:
                return SendOutcome("failed", error="feishu_message_rejected")
            if not isinstance(data.get("data"), dict):
                return SendOutcome("uncertain", error="feishu_acknowledgement_invalid")
            identity = _text(data["data"].get("message_id"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except _HTTPRejected:
            return SendOutcome("failed", error="feishu_http_rejected_message")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="feishu_send_not_confirmed")

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        try:
            secrets = _secrets(credential)
            _settings(channel, secrets)
            _text(destination)
            _text(delivery_key, maximum=512)
            if not content.text or len(content.text.encode()) > 20000 or content.attachments:
                return SendOutcome("failed", error="feishu_content_requires_owned_chunk_or_attachment_delivery")
            token = await self._token(secrets)
        except (InvalidInput, AccessDenied, httpx.HTTPError, TimeoutError):
            return SendOutcome("failed", error="feishu_configuration_or_authentication_failed")
        try:
            data = await self._request(self._post("/open-apis/im/v1/messages?receive_id_type=chat_id",
                {"receive_id": destination, "msg_type": "text", "content": json.dumps({"text": content.text}, ensure_ascii=False),
                 "uuid": hashlib.sha256(delivery_key.encode()).hexdigest()[:32]}, token))
            if type(data.get("code")) is not int:
                return SendOutcome("uncertain", error="feishu_acknowledgement_invalid")
            if data["code"] != 0:
                return SendOutcome("failed", error="feishu_message_rejected")
            detail = data.get("data")
            if not isinstance(detail, dict):
                return SendOutcome("uncertain", error="feishu_acknowledgement_invalid")
            identity = _text(detail.get("message_id"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except _HTTPRejected:
            return SendOutcome("failed", error="feishu_http_rejected_message")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="feishu_send_not_confirmed")

    async def listen(self, channel: ChannelView, credential: Secret,
            on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        from app.modules.channel.transport import listen_transport
        await listen_transport(self._listen(channel, credential, on_message))

    async def _listen(self, channel: ChannelView, credential: Secret,
            on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        """Own one authenticated connection until cancellation; caller owns reconnect policy."""
        secrets = _secrets(credential)
        settings = _settings(channel, secrets)
        if settings.connection_mode != "websocket":
            raise InvalidInput("Feishu long connection is not configured")
        data = await self._request(self._post("/callback/ws/endpoint", {"AppID": secrets["app_id"], "AppSecret": secrets["app_secret"]}))
        if data.get("code") != 0 or not isinstance(data.get("data"), dict):
            raise AccessDenied("Feishu connection authentication failed")
        url = _text(data["data"].get("URL"), maximum=8192)
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or parsed.username or not parsed.hostname or not (
                parsed.hostname.endswith(".feishu.cn") or parsed.hostname.endswith(".larksuite.com")):
            raise AccessDenied("Feishu connection endpoint is invalid")
        service = parse_qs(parsed.query).get("service_id", [""])[0]
        if not service.isdigit():
            raise InvalidInput("Feishu connection has no service identity")
        config = data["data"].get("ClientConfig", {})
        interval = config.get("PingInterval", 120) if isinstance(config, dict) else 120
        if type(interval) is not int or not 1 <= interval <= 600:
            raise InvalidInput("Feishu heartbeat interval is invalid")
        # The official SDK supplies the provider protobuf wire type, not a lifecycle controller.
        from lark_oapi.ws.pb.pbbp2_pb2 import Frame

        fragments: dict[str, tuple[float, int, dict[int, bytes]]] = {}
        async with self.connector(url) as socket:
            last_pong = time.monotonic()
            async def ping() -> None:
                while True:
                    if time.monotonic() - last_pong > 3 * interval:
                        raise ConnectionError("Feishu heartbeat was not acknowledged")
                    frame = Frame(SeqID=0, LogID=0, service=int(service), method=0)
                    frame.headers.add(key="type", value="ping")
                    await socket.send(frame.SerializeToString())
                    await asyncio.sleep(interval)
            ping_task = asyncio.create_task(ping(), name="feishu-channel-ping")
            async def next_frame() -> str | bytes:
                reading = asyncio.create_task(socket.recv(), name="feishu-channel-read")
                try:
                    done, _ = await asyncio.wait((reading, ping_task), return_when=asyncio.FIRST_COMPLETED)
                    if ping_task in done:
                        await ping_task
                        raise ConnectionError("Feishu heartbeat ended")
                    return reading.result()
                finally:
                    reading.cancel()
                    await asyncio.gather(reading, return_exceptions=True)
            try:
                while True:
                    raw = await next_frame()
                    if not isinstance(raw, bytes) or len(raw) > _MAX:
                        raise InvalidInput("Feishu connection frame is invalid")
                    frame = Frame()
                    frame.ParseFromString(raw)
                    headers = {item.key: item.value for item in frame.headers}
                    if frame.method == 0:
                        if headers.get("type") == "pong":
                            last_pong = time.monotonic()
                            if frame.payload:
                                updated = _json(frame.payload).get("PingInterval", interval)
                                if type(updated) is not int or not 1 <= updated <= 600:
                                    raise InvalidInput("Feishu heartbeat configuration is invalid")
                                interval = updated
                        continue
                    if frame.method != 1:
                        raise InvalidInput("Feishu frame method is unsupported")
                    if headers.get("type") != "event":
                        continue
                    try:
                        total, sequence = int(headers.get("sum", "1")), int(headers.get("seq", "0"))
                    except ValueError:
                        raise InvalidInput("Feishu fragment indices are invalid") from None
                    if not 1 <= total <= 64 or not 0 <= sequence < total:
                        raise InvalidInput("Feishu fragment count is invalid")
                    payload = frame.payload
                    if total > 1:
                        stamp = time.monotonic()
                        fragments = {key: value for key, value in fragments.items() if stamp - value[0] < 30}
                        key = _text(headers.get("message_id"))
                        if key not in fragments:
                            if len(fragments) >= 16:
                                raise InvalidInput("Feishu fragment assembly capacity exceeded")
                            fragments[key] = (stamp, total, {})
                        _, expected, parts = fragments[key]
                        if expected != total or (sequence in parts and parts[sequence] != payload):
                            raise InvalidInput("Feishu fragment identity changed")
                        parts[sequence] = payload
                        if sum(len(part) for _, _, collection in fragments.values() for part in collection.values()) > _MAX:
                            raise InvalidInput("Feishu fragments exceed their byte bound")
                        if len(parts) < total:
                            continue
                        payload = b"".join(parts[index] for index in range(total))
                        del fragments[key]
                    incoming = _normalize(channel, settings, _json(payload))
                    if incoming.message is not None:
                        await on_message(incoming)
                    frame.payload = b'{"code":200}'
                    await socket.send(frame.SerializeToString())
            finally:
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
                fragments.clear()
