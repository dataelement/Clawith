"""WeCom encrypted callbacks, application HTTP and authenticated AI-bot sockets."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import struct
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import SecretStr, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

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
    WebhookReply,
)
from app.modules.channel.reply_context import MediaContext, ReplyContext
from app.modules.channel.settings import WeComSettings
from app.modules.channel.transport import protect_request_url
from app.modules.credential.public import Secret

_MAX = 256 * 1024
_API = "https://qyapi.weixin.qq.com/cgi-bin"
_HEARTBEAT_SECONDS = 30.0
_WIRE_LOGGER = logging.Logger("clawith.channel.wecom.wire", level=logging.WARNING)  # noqa: LOG001 -- isolated wire logger must not inherit DEBUG and reveal subscription Secrets.


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
        raise InvalidInput("WeCom field exceeds its supported shape or bound")
    return value


def _json(raw: str | bytes) -> dict[str, Any]:
    # Provider JSON is untyped until the consumed fields are checked below.
    if len(raw.encode() if isinstance(raw, str) else raw) > _MAX:
        raise InvalidInput("WeCom payload exceeds its byte bound")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("WeCom payload is invalid") from None
    if not isinstance(value, dict):
        raise InvalidInput("WeCom payload must be an object")
    return value


def _configuration(channel: ChannelView, credential: Secret) -> tuple[WeComSettings, dict[str, Any]]:
    if channel.provider != "wecom" or not channel.enabled:
        raise AccessDenied("WeCom channel is unavailable")
    try:
        settings = WeComSettings.model_validate_json(channel.settings_json)
    except ValidationError:
        raise InvalidInput("WeCom settings are invalid") from None
    secrets = _json(credential.value)
    expected = {"version", "bot_secret"} if settings.connection_mode == "websocket" else {
        "version", "corp_secret", "verification_token", "encoding_aes_key"}
    if set(secrets) != expected or type(secrets["version"]) is not int or secrets["version"] != 1:
        raise InvalidInput("WeCom Credential bundle is unsupported")
    for key in expected - {"version"}:
        _text(secrets[key], maximum=8192)
    if settings.connection_mode == "webhook" and channel.external_identity != f"{settings.corp_id}:{settings.agent_id}":
        raise AccessDenied("WeCom channel application identity is invalid")
    if settings.connection_mode == "customer_service" and channel.external_identity != f"{settings.corp_id}:kf:{settings.open_kfid}":
        raise AccessDenied("WeCom customer-service identity is invalid")
    return settings, secrets


def _xml(raw: bytes) -> ET.Element:
    if len(raw) > _MAX or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise InvalidInput("WeCom XML exceeds its supported format")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise InvalidInput("WeCom XML is invalid") from None


def _aes_key(encoded: str) -> bytes:
    try:
        if len(encoded) != 43:
            raise ValueError
        result = base64.b64decode(encoded + "=", validate=True)
        if len(result) != 32:
            raise ValueError
        return result
    except ValueError:
        raise InvalidInput("WeCom AES key is invalid") from None


def decrypt_callback(encoded: str, encrypted: str, *, expected_corp_id: str) -> bytes:
    key = _aes_key(encoded)
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        if not ciphertext or len(ciphertext) > _MAX or len(ciphertext) % 16:
            raise ValueError
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(256).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        if len(plain) < 20:
            raise ValueError
        length = struct.unpack("!I", plain[16:20])[0]
        if length > len(plain) - 20:
            raise ValueError
        if not hmac.compare_digest(plain[20 + length:], expected_corp_id.encode()):
            raise AccessDenied("WeCom callback belongs to another corporation")
        return plain[20:20 + length]
    except ValueError:
        raise AccessDenied("WeCom encrypted callback is invalid") from None


def _authenticated_callback(settings: WeComSettings, secrets: dict[str, Any], *, body: bytes,
        headers: dict[str, str], now: datetime) -> tuple[bytes, bool]:
    if settings.connection_mode not in ("webhook", "customer_service") or now.tzinfo is None:
        raise AccessDenied("WeCom webhook is unavailable")
    stamp, nonce, signature = headers.get("timestamp", ""), headers.get("nonce", ""), headers.get("msg_signature", "")
    if not stamp.isdigit() or len(stamp) > 16 or abs(now.timestamp() - int(stamp)) > 300 or not nonce or len(nonce) > 512:
        raise AccessDenied("WeCom callback timestamp or nonce is invalid")
    challenge = headers.get("echostr")
    encrypted = _text(challenge, maximum=_MAX) if challenge is not None else _text(_xml(body).findtext("Encrypt"), maximum=_MAX)
    expected = hashlib.sha1("".join(sorted((secrets["verification_token"], stamp, nonce, encrypted))).encode()).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AccessDenied("WeCom callback signature is invalid")
    assert settings.corp_id is not None
    return decrypt_callback(secrets["encoding_aes_key"], encrypted, expected_corp_id=settings.corp_id), challenge is not None


@dataclass(frozen=True, slots=True)
class CustomerServiceNotice:
    event_id: str
    open_kfid: str
    token: Secret = field(repr=False)


@dataclass(frozen=True, slots=True)
class CustomerServicePage:
    messages: tuple[IncomingMessage, ...]
    next_cursor: Secret | None = field(repr=False)


def customer_service_notice(channel: ChannelView, credential: Secret, *, body: bytes,
        headers: dict[str, str], now: datetime) -> CustomerServiceNotice:
    """Authenticate a sync notice; Channel owns durable notice acceptance and pagination."""
    settings, secrets = _configuration(channel, credential)
    decoded, challenge = _authenticated_callback(settings, secrets, body=body, headers=headers, now=now)
    value = _xml(decoded)
    if challenge or value.findtext("ToUserName") != settings.corp_id or value.findtext("Event") != "kf_msg_or_event":
        raise InvalidInput("WeCom callback is not a customer-service notice")
    token = _text(value.findtext("Token"), maximum=8192)
    identity = _text(value.findtext("OpenKfId"))
    if settings.connection_mode == "customer_service" and identity != settings.open_kfid:
        raise AccessDenied("WeCom notice belongs to another customer-service account")
    return CustomerServiceNotice("kf:" + hashlib.sha256((identity + "\0" + token).encode()).hexdigest(), identity, Secret(token))


def _socket_message(channel: ChannelView, value: dict[str, Any]) -> InboundResult:
    if value.get("aibotid") != channel.external_identity:
        raise AccessDenied("WeCom socket message belongs to another bot")
    source = value.get("from")
    if not isinstance(source, dict):
        raise InvalidInput("WeCom socket sender is invalid")
    actor = _text(source.get("userid"))
    kind = value.get("msgtype")
    if value.get("chattype") not in ("single", "group"):
        raise InvalidInput("WeCom socket conversation type is invalid")
    group = _text(value.get("chatid")) if value["chattype"] == "group" else None
    event_id = _text(value.get("msgid"))
    media: list[MediaContext] = []
    references: list[AttachmentReference] = []
    def attachment(kind: str, item: object) -> None:
        if not isinstance(item, dict):
            raise InvalidInput("WeCom attachment is invalid")
        reference = f"wecom:{event_id}:media:{len(media)}"
        name = _text(item.get("name", kind))
        try:
            private = MediaContext(reference_id=reference,
                download_url=SecretStr(_text(item.get("url"), maximum=8192)),
                aes_key=SecretStr(_text(item.get("aeskey"), maximum=256)), name=name, media_type=None)
        except ValidationError:
            raise InvalidInput("WeCom private media metadata is invalid") from None
        media.append(private)
        references.append(AttachmentReference(reference, name, None))
    if kind in ("text", "voice"):
        content = value.get(kind)
        if not isinstance(content, dict):
            raise InvalidInput("WeCom socket message content is invalid")
        text = _text(content.get("content"), maximum=_MAX, empty=True)
    elif kind == "mixed":
        mixed = value.get("mixed")
        if not isinstance(mixed, dict) or not isinstance(mixed.get("msg_item"), list) or len(mixed["msg_item"]) > 64:
            raise InvalidInput("WeCom mixed message is invalid")
        parts = []
        for part in mixed["msg_item"]:
            if not isinstance(part, dict):
                raise InvalidInput("WeCom mixed message item is invalid")
            if part.get("msgtype") == "text" and isinstance(part.get("text"), dict):
                parts.append(_text(part["text"].get("content"), maximum=_MAX, empty=True))
            elif part.get("msgtype") == "image":
                attachment("image", part.get("image"))
            else:
                raise InvalidInput("WeCom mixed message item type is unsupported")
        text = "\n".join(parts)
    elif kind in ("image", "file", "video"):
        attachment(kind, value.get(kind))
        text = ""
    else:
        return InboundResult()
    if len(text.encode()) > _MAX:
        raise InvalidInput("WeCom normalized message exceeds its byte bound")
    normalized = IncomingMessage(event_id, actor, group or actor, group, text, None, tuple(references))
    if len(json.dumps(asdict(normalized), ensure_ascii=False).encode()) > _MAX:
        raise InvalidInput("WeCom normalized event exceeds its complete byte bound")
    return InboundResult(message=normalized,
        private_context=ReplyContext(provider="wecom", conversation_id=group or actor, media=tuple(media)) if media else None,
        context_expires_at=datetime.now(UTC) + timedelta(minutes=5) if media else None)


@dataclass(slots=True)
class _Connection:
    socket: _Socket
    pending: dict[str, asyncio.Future[dict[str, Any]]]


class WeComAdapter:
    provider: Provider = "wecom"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10, connector: Connector = _connect) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("WeCom HTTP deadline is invalid")
        self.http, self.timeout, self.connector = http, timeout_seconds, connector
        self._connections: dict[UUID, _Connection] = {}
        self._starting: set[UUID] = set()

    async def receive_customer_service_notice(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> CustomerServiceNotice:
        return customer_service_notice(channel, credential, body=body, headers=headers, now=now)

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> InboundResult:
        settings, secrets = _configuration(channel, credential)
        # The HTTP adapter supplies these authenticated callback query fields separately from body data.
        decoded, challenge = _authenticated_callback(settings, secrets, body=body, headers=headers, now=now)
        if challenge:
            try:
                plaintext = decoded.decode()
            except UnicodeError:
                raise InvalidInput("WeCom verification challenge is invalid") from None
            return InboundResult(reply=WebhookReply(200, "text/plain", plaintext))
        if settings.connection_mode == "customer_service":
            raise InvalidInput("Customer-service events require the dedicated synchronization intake")
        value = _xml(decoded)
        if value.findtext("Event") == "kf_msg_or_event":
            raise InvalidInput("WeCom customer-service notice requires its durable sync intake")
        if value.findtext("ToUserName") != settings.corp_id or value.findtext("AgentID") != str(settings.agent_id):
            raise AccessDenied("WeCom message belongs to another application")
        kind = value.findtext("MsgType")
        if kind == "event":
            return InboundResult()
        actor, message_id = _text(value.findtext("FromUserName")), _text(value.findtext("MsgId"))
        chat = value.findtext("ChatId")
        attachments: tuple[AttachmentReference, ...] = ()
        if kind == "text":
            text = _text(value.findtext("Content", ""), maximum=_MAX, empty=True)
        elif kind in ("image", "voice", "video", "file"):
            text = _text(value.findtext("Recognition", ""), maximum=_MAX, empty=True) if kind == "voice" else ""
            attachments = (AttachmentReference(_text(value.findtext("MediaId")),
                _text(value.findtext("Title") or kind), None),)
        else:
            raise InvalidInput("WeCom application message type is unsupported")
        normalized = IncomingMessage(message_id, actor, _text(chat) if chat else actor,
            _text(chat) if chat else None, text, None, attachments)
        if len(json.dumps(asdict(normalized), ensure_ascii=False).encode()) > _MAX:
            raise InvalidInput("WeCom normalized event exceeds its complete byte bound")
        return InboundResult(message=normalized)

    async def _request(self, request: httpx.Request) -> dict[str, Any]:
        require_stateless_http_client(self.http)
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(protect_request_url(request), stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    if response.status_code in (400, 401, 403, 404, 422, 429):
                        raise _HTTPRejected("WeCom HTTP operation was rejected")
                    raise InvalidInput("WeCom HTTP operation was rejected")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > _MAX:
                        raise InvalidInput("WeCom response exceeds its byte bound")
                    raw.extend(chunk)
                return _json(bytes(raw))
            finally:
                await response.aclose()

    async def download_media(self, context: MediaContext, *, maximum: int = 16 * 1024 * 1024) -> bytes:
        """Channel authorizes and decrypts the private context before passing it to this transport."""
        if type(maximum) is not int or not 1 <= maximum <= 16 * 1024 * 1024:
            raise InvalidInput("WeCom download bound is invalid")
        request = httpx.Request("GET", context.download_url.get_secret_value(),
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
        require_stateless_http_client(self.http)
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(protect_request_url(request), stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("WeCom media download was rejected")
                ciphertext = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(ciphertext) + len(chunk) > maximum + 32:
                        raise InvalidInput("WeCom encrypted media exceeds its byte bound")
                    ciphertext.extend(chunk)
            finally:
                await response.aclose()
        try:
            encoded_key = context.aes_key.get_secret_value()
            key = base64.b64decode(encoded_key + "=" * (-len(encoded_key) % 4), validate=True)
            if len(key) != 32 or not ciphertext or len(ciphertext) % 16:
                raise ValueError
            decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
            plain = decryptor.update(bytes(ciphertext)) + decryptor.finalize()
            size = plain[-1]
            if not 1 <= size <= 32 or plain[-size:] != bytes([size]) * size:
                raise ValueError
            plain = plain[:-size]
            if len(plain) > maximum:
                raise InvalidInput("WeCom media exceeds its plaintext bound")
            return plain
        except ValueError:
            raise InvalidInput("WeCom media ciphertext or key is invalid") from None

    async def _application_token(self, settings: WeComSettings, secrets: dict[str, Any]) -> str:
        if settings.connection_mode not in ("webhook", "customer_service"):
            raise InvalidInput("WeCom application HTTP is not configured")
        request = httpx.Request("GET", _API + "/gettoken", params={"corpid": settings.corp_id, "corpsecret": secrets["corp_secret"]},
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
        data = await self._request(request)
        if type(data.get("errcode")) is not int or data["errcode"] != 0:
            raise InvalidInput("WeCom application token was rejected")
        return _text(data.get("access_token"), maximum=8192)

    async def sync_customer_service(self, channel: ChannelView, credential: Secret, *, open_kfid: str,
            event_token: Secret | None = None, cursor: Secret | None = None, limit: int = 20) -> CustomerServicePage:
        """Fetch one bounded native page; caller owns its cursor, idempotency and product intake."""
        settings, secrets = _configuration(channel, credential)
        if settings.connection_mode == "customer_service" and open_kfid != settings.open_kfid:
            raise AccessDenied("WeCom synchronization belongs to another customer-service account")
        _text(open_kfid)
        if (event_token is None) == (cursor is None) or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("WeCom customer-service cursor or page size is invalid")
        coordinate = event_token or cursor
        assert coordinate is not None
        _text(coordinate.value, maximum=8192)
        token = await self._application_token(settings, secrets)
        request = httpx.Request("POST", _API + "/kf/sync_msg", params={"access_token": token},
            json={"open_kfid": open_kfid, "limit": limit, "token" if event_token is not None else "cursor": coordinate.value},
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
        data = await self._request(request)
        if type(data.get("errcode")) is not int or data["errcode"] != 0:
            raise InvalidInput("WeCom customer-service synchronization was rejected")
        messages = data.get("msg_list")
        if not isinstance(messages, list) or len(messages) > limit or type(data.get("has_more")) is not int or data["has_more"] not in (0, 1):
            raise InvalidInput("WeCom customer-service page is invalid")
        received = []
        for message in messages:
            if not isinstance(message, dict):
                raise InvalidInput("WeCom customer-service message is invalid")
            if message.get("origin") != 3 or message.get("msgtype") != "text":
                continue
            if message.get("open_kfid") != open_kfid or not isinstance(message.get("text"), dict):
                raise AccessDenied("WeCom customer-service message has another destination")
            actor = _text(message.get("external_userid"))
            received.append(IncomingMessage(_text(message.get("msgid")), actor, f"kf:{open_kfid}:{actor}", None,
                _text(message["text"].get("content"), maximum=_MAX, empty=True), None))
        next_cursor = Secret(_text(data.get("next_cursor"), maximum=8192)) if data["has_more"] else None
        if cursor is not None and next_cursor is not None and next_cursor.value == cursor.value:
            raise InvalidInput("WeCom customer-service cursor did not advance")
        return CustomerServicePage(tuple(received), next_cursor)

    async def send_customer_service(self, channel: ChannelView, credential: Secret, *, open_kfid: str,
            destination: str, text: str, delivery_key: str) -> SendOutcome:
        try:
            settings, secrets = _configuration(channel, credential)
            if settings.connection_mode == "customer_service" and open_kfid != settings.open_kfid:
                raise AccessDenied("WeCom send belongs to another customer-service account")
            _text(open_kfid)
            _text(destination)
            _text(text, maximum=2048)
            _text(delivery_key)
            token = await self._application_token(settings, secrets)
        except (InvalidInput, AccessDenied, httpx.HTTPError, TimeoutError):
            return SendOutcome("failed", error="wecom_customer_service_configuration_failed")
        try:
            data = await self._request(httpx.Request("POST", _API + "/kf/send_msg", params={"access_token": token},
                json={"touser": destination, "open_kfid": open_kfid, "msgid": hashlib.sha256(delivery_key.encode()).hexdigest()[:32],
                    "msgtype": "text", "text": {"content": text}},
                extensions={"timeout": httpx.Timeout(self.timeout).as_dict()}))
            if type(data.get("errcode")) is not int:
                return SendOutcome("uncertain", error="wecom_customer_service_acknowledgement_invalid")
            if data["errcode"] != 0:
                return SendOutcome("failed", error="wecom_customer_service_message_rejected")
            identity = _text(data.get("msgid"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except _HTTPRejected:
            return SendOutcome("failed", error="wecom_customer_service_http_rejected")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="wecom_customer_service_send_not_confirmed")

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        try:
            settings, secrets = _configuration(channel, credential)
            _text(destination)
            _text(delivery_key, maximum=512)
            if not content.text or content.attachments:
                return SendOutcome("failed", error="wecom_attachment_delivery_requires_materialization")
            if destination.startswith("kf:"):
                coordinates = destination.split(":", 2)
                if len(coordinates) != 3 or not coordinates[1] or not coordinates[2]:
                    return SendOutcome("failed", error="wecom_customer_service_destination_invalid")
                return await self.send_customer_service(channel, credential, open_kfid=coordinates[1],
                    destination=coordinates[2], text=content.text, delivery_key=delivery_key)
            if settings.connection_mode == "websocket":
                return await self._send_socket(channel, destination, content.text, delivery_key)
            if settings.connection_mode == "customer_service":
                return SendOutcome("failed", error="wecom_customer_service_destination_required")
            if len(content.text.encode()) > 2048:
                return SendOutcome("failed", error="wecom_text_requires_owned_chunking")
            token = await self._application_token(settings, secrets)
        except (InvalidInput, AccessDenied, httpx.HTTPError, TimeoutError):
            return SendOutcome("failed", error="wecom_configuration_or_authentication_failed")
        try:
            request = httpx.Request("POST", _API + "/message/send", params={"access_token": token},
                json={"touser": destination, "agentid": settings.agent_id, "msgtype": "text", "text": {"content": content.text}},
                extensions={"timeout": httpx.Timeout(self.timeout).as_dict()})
            data = await self._request(request)
            if type(data.get("errcode")) is not int:
                return SendOutcome("uncertain", error="wecom_acknowledgement_invalid")
            if data["errcode"] != 0 or data.get("invaliduser") or data.get("unlicenseduser"):
                return SendOutcome("failed", error="wecom_message_rejected")
            identity = _text(data.get("msgid"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except _HTTPRejected:
            return SendOutcome("failed", error="wecom_http_rejected_message")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="wecom_send_not_confirmed")

    async def _send_socket(self, channel: ChannelView, destination: str, text: str, key: str) -> SendOutcome:
        connection = self._connections.get(channel.id)
        if connection is None:
            return SendOutcome("failed", error="wecom_connection_unavailable")
        if len(text.encode()) > 20000 or len(connection.pending) >= 32:
            return SendOutcome("failed", error="wecom_send_capacity_or_payload_exceeded")
        request_id = "send_" + hashlib.sha256(key.encode()).hexdigest()[:32]
        if request_id in connection.pending:
            return SendOutcome("uncertain", error="wecom_delivery_already_pending")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        connection.pending[request_id] = future
        try:
            async with asyncio.timeout(self.timeout):
                await connection.socket.send(json.dumps({"cmd": "aibot_send_msg", "headers": {"req_id": request_id},
                    "body": {"chatid": destination, "msgtype": "markdown", "markdown": {"content": text}}}))
                data = await future
            if type(data.get("errcode")) is not int:
                return SendOutcome("uncertain", error="wecom_acknowledgement_invalid")
            return SendOutcome("delivered", acknowledgement=request_id) if data["errcode"] == 0 else SendOutcome("failed", error="wecom_message_rejected")
        except (TimeoutError, OSError, ConnectionClosed):
            return SendOutcome("uncertain", error="wecom_send_not_confirmed")
        finally:
            connection.pending.pop(request_id, None)

    async def listen(self, channel: ChannelView, credential: Secret,
            on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        """Own one authenticated socket; cancellation drains heartbeat and pending sends."""
        settings, secrets = _configuration(channel, credential)
        if settings.connection_mode != "websocket" or channel.id in self._connections or channel.id in self._starting:
            raise InvalidInput("WeCom connection is not available for startup")
        self._starting.add(channel.id)
        try:
            from app.modules.channel.transport import listen_transport
            await listen_transport(self._listen_one(channel, secrets, on_message))
        finally:
            self._starting.discard(channel.id)

    async def _listen_one(self, channel: ChannelView, secrets: dict[str, Any],
            on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        async with self.connector("wss://openws.work.weixin.qq.com") as socket:
            authentication_id = "subscribe_" + uuid4().hex
            await socket.send(json.dumps({"cmd": "aibot_subscribe", "headers": {"req_id": authentication_id},
                "body": {"bot_id": channel.external_identity, "secret": secrets["bot_secret"]}}))
            async with asyncio.timeout(self.timeout):
                acknowledgement = _json(await socket.recv())
            if (not isinstance(acknowledgement.get("headers"), dict) or acknowledgement["headers"].get("req_id") != authentication_id
                    or type(acknowledgement.get("errcode")) is not int or acknowledgement["errcode"] != 0):
                raise AccessDenied("WeCom connection authentication failed")
            connection = _Connection(socket, {})
            self._connections[channel.id] = connection
            incoming_queue: asyncio.Queue[InboundResult] = asyncio.Queue(maxsize=16)
            last_pong = time.monotonic()
            async def ping() -> None:
                while True:
                    await asyncio.sleep(_HEARTBEAT_SECONDS)
                    if time.monotonic() - last_pong > 3 * _HEARTBEAT_SECONDS:
                        raise ConnectionError("WeCom heartbeat was not acknowledged")
                    await socket.send(json.dumps({"cmd": "ping", "headers": {"req_id": "ping_" + uuid4().hex}}))
            async def consume() -> None:
                while True:
                    await on_message(await incoming_queue.get())

            async def receive() -> None:
                nonlocal last_pong
                while True:
                    frame = _json(await socket.recv())
                    headers = frame.get("headers")
                    if not isinstance(headers, dict):
                        raise InvalidInput("WeCom frame headers are invalid")
                    request_id = _text(headers.get("req_id"))
                    if request_id.startswith("ping_"):
                        if type(frame.get("errcode")) is int and frame["errcode"] == 0:
                            last_pong = time.monotonic()
                        continue
                    pending = connection.pending.get(request_id)
                    if pending is not None:
                        if not pending.done():
                            pending.set_result(frame)
                        continue
                    if frame.get("cmd") != "aibot_msg_callback":
                        continue
                    body = frame.get("body")
                    if not isinstance(body, dict):
                        raise InvalidInput("WeCom callback body is invalid")
                    incoming = _socket_message(channel, body)
                    if incoming.message is not None:
                        try:
                            incoming_queue.put_nowait(incoming)
                        except asyncio.QueueFull:
                            raise InvalidInput("WeCom input capacity exceeded; connection must resynchronize") from None
            try:
                # Keep acknowledgements flowing even when product intake sends a reply on this socket.
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(ping(), name="wecom-channel-ping")
                    tasks.create_task(consume(), name="wecom-channel-input")
                    await receive()
            finally:
                self._connections.pop(channel.id, None)
                for future in connection.pending.values():
                    if not future.done():
                        future.set_exception(OSError("WeCom connection closed"))
                connection.pending.clear()
