"""WeChat iLink QR enrollment, long polling and bounded contextual text delivery."""

import asyncio
import base64
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr, ValidationError

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
from app.modules.channel.adapters import _json, _text
from app.modules.channel.contracts import (
    ChannelView,
    DeliveryContent,
    InboundResult,
    IncomingMessage,
    Provider,
    SendOutcome,
)
from app.modules.channel.reply_context import ReplyContext
from app.modules.channel.settings import WeChatSettings
from app.modules.channel.transport import protect_request_url
from app.modules.credential.public import Secret

_BASE = "https://ilinkai.weixin.qq.com"
_STATUSES = frozenset({"wait", "scaned", "confirmed", "expired", "scaned_but_redirect", "need_verifycode", "verify_code_blocked", "binded_redirect"})
logger = logging.getLogger(__name__)


class WeChatSessionExpired(AccessDenied):
    code = "wechat_session_expired"


class _Rejected(InvalidInput):
    pass


@dataclass(frozen=True, slots=True)
class QRChallenge:
    qrcode: SecretStr
    image_url: SecretStr


@dataclass(frozen=True, slots=True)
class QRStatus:
    status: str
    bot_token: SecretStr | None = None
    bot_id: str | None = None
    user_id: str | None = None
    base_url: str | None = None
    redirect_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class PollBatch:
    messages: tuple[InboundResult, ...]
    next_cursor: SecretStr


def _wechat_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".weixin.qq.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise InvalidInput("WeChat API endpoint is invalid")
    return value.rstrip("/")


def _configuration(channel: ChannelView, credential: Secret):
    if channel.provider != "wechat" or not channel.enabled:
        raise AccessDenied("WeChat Channel is unavailable")
    try:
        settings = WeChatSettings.model_validate_json(channel.settings_json)
    except ValidationError:
        raise InvalidInput("WeChat settings are invalid") from None
    _wechat_url(settings.base_url)
    data = _json(credential.value)
    if set(data) - {"version", "bot_token", "route_tag"} or type(data.get("version")) is not int or data["version"] != 1:
        raise InvalidInput("WeChat Credential bundle is invalid")
    token = _text(data.get("bot_token"), maximum=16384)
    route = _text(data["route_tag"], maximum=512) if data.get("route_tag") else None
    return settings, token, route


def _headers(*, token: str | None = None, route: str | None = None, version: str = "1.0.0") -> dict[str, str]:
    try:
        parts = tuple(int(part) for part in version.split("."))
        if len(parts) != 3 or any(not 0 <= part <= 255 for part in parts):
            raise ValueError()
    except ValueError:
        raise InvalidInput("WeChat channel version is invalid") from None
    headers = {"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(str(int.from_bytes(os.urandom(4), "big")).encode()).decode(),
        "iLink-App-Id": "bot", "iLink-App-ClientVersion": str((parts[0] << 16) | (parts[1] << 8) | parts[2])}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if route is not None:
        headers["SKRouteTag"] = route
    return headers


def _success(data: dict) -> None:
    if data.get("ret") == -14 or data.get("errcode") == -14:
        raise WeChatSessionExpired("WeChat session expired; sign in again")
    if data.get("ret", 0) not in (0, None) or data.get("errcode", 0) not in (0, None):
        raise _Rejected("WeChat operation was rejected")


class WeChatAdapter:
    provider: Provider = "wechat"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 20, poll_timeout_seconds: float = 40) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120 or not 0 < poll_timeout_seconds <= 60:
            raise InvalidInput("WeChat operation deadline is invalid")
        self.http, self.timeout, self.poll_timeout = http, timeout_seconds, poll_timeout_seconds
        self._listening: set[UUID] = set()

    async def _request(self, method: str, url: str, *, headers: dict[str, str], payload=None, polling=False) -> dict:
        require_stateless_http_client(self.http)
        timeout = self.poll_timeout if polling else self.timeout
        request = protect_request_url(httpx.Request(method, url, headers=headers, json=payload,
            extensions={"timeout": httpx.Timeout(timeout).as_dict()}))
        async with asyncio.timeout(timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if 400 <= response.status_code < 500:
                    raise _Rejected("WeChat request was rejected")
                if response.status_code != 200:
                    raise InvalidInput("WeChat response was not confirmed")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result) + len(chunk) > 262144:
                        raise InvalidInput("WeChat response exceeds its bound")
                    result.extend(chunk)
                return _json(bytes(result))
            finally:
                await response.aclose()

    async def create_qr(self, *, route_tag: SecretStr | None = None) -> QRChallenge:
        data = await self._request("POST", _BASE + "/ilink/bot/get_bot_qrcode?bot_type=3",
            headers=_headers(route=route_tag.get_secret_value() if route_tag else None), payload={"local_token_list": []})
        _success(data)
        return QRChallenge(SecretStr(_text(data.get("qrcode"), maximum=8192)), SecretStr(_text(data.get("qrcode_img_content"), maximum=8192)))

    async def qr_status(self, qrcode: SecretStr, *, route_tag: SecretStr | None = None, verify_code: SecretStr | None = None,
                         base_url: str = _BASE) -> QRStatus:
        qr = _text(qrcode.get_secret_value(), maximum=8192)
        url = _wechat_url(base_url) + "/ilink/bot/get_qrcode_status?qrcode=" + quote(qr, safe="")
        if verify_code is not None:
            url += "&verify_code=" + quote(_text(verify_code.get_secret_value(), maximum=64), safe="")
        data = await self._request("GET", url, headers=_headers(route=route_tag.get_secret_value() if route_tag else None), polling=True)
        _success(data)
        status = data.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise InvalidInput("WeChat QR status is unsupported")
        if status == "confirmed":
            return QRStatus(status, SecretStr(_text(data.get("bot_token"), maximum=16384)),
                _text(data.get("ilink_bot_id"), maximum=512), _text(data["ilink_user_id"], maximum=512) if data.get("ilink_user_id") else None,
                _wechat_url(_text(data.get("baseurl", base_url), maximum=2048)))
        if status == "scaned_but_redirect":
            host = _text(data.get("redirect_host"), maximum=512)
            return QRStatus(status, redirect_base_url=_wechat_url("https://" + host))
        return QRStatus(status)

    async def qr_image(self, image_url: SecretStr) -> tuple[bytes, str]:
        url = _text(image_url.get_secret_value(), maximum=8192)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ("liteapp.weixin.qq.com", "weixin.qq.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise InvalidInput("WeChat QR image endpoint is invalid")
        require_stateless_http_client(self.http)
        request = protect_request_url(httpx.Request("GET", url, extensions={"timeout": httpx.Timeout(self.timeout).as_dict()}))
        async with asyncio.timeout(self.timeout):
            response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                media = response.headers.get("content-type", "").split(";", 1)[0]
                if response.status_code != 200:
                    raise InvalidInput("WeChat QR image was not confirmed")
                if media not in ("image/png", "image/jpeg", "image/webp"):
                    # The provider may supply QR content rather than an image; never proxy its HTML.
                    return url.encode(), "text/plain"
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result) + len(chunk) > 2 * 1024 * 1024:
                        raise InvalidInput("WeChat QR image exceeds its bound")
                    result.extend(chunk)
                return bytes(result), media
            finally:
                await response.aclose()

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes, headers: dict[str, str], now: datetime) -> InboundResult:
        raise AccessDenied("WeChat input is accepted only through authenticated iLink polling")

    async def poll_once(self, channel: ChannelView, credential: Secret, *, cursor: SecretStr, now: datetime) -> PollBatch:
        settings, token, route = _configuration(channel, credential)
        if now.tzinfo is None:
            raise InvalidInput("WeChat observation time requires a timezone")
        value = cursor.get_secret_value()
        if len(value.encode()) > 65536:
            raise InvalidInput("WeChat polling cursor exceeds its bound")
        data = await self._request("POST", settings.base_url.rstrip("/") + "/ilink/bot/getupdates", polling=True,
            headers=_headers(token=token, route=route, version=settings.channel_version),
            payload={"get_updates_buf": value, "base_info": {"channel_version": settings.channel_version, "bot_agent": "Clawith/1"}})
        _success(data)
        messages = data.get("msgs", [])
        next_cursor = data.get("get_updates_buf", value)
        if not isinstance(messages, list) or len(messages) > 100 or not isinstance(next_cursor, str) or len(next_cursor.encode()) > 65536:
            raise InvalidInput("WeChat update batch is invalid")
        accepted = []
        for message in messages:
            if not isinstance(message, dict):
                raise InvalidInput("WeChat message is invalid")
            if message.get("message_type") != 1 or message.get("from_user_id") == channel.external_identity:
                continue
            if message.get("to_user_id") != channel.external_identity:
                raise AccessDenied("WeChat update belongs to another bot")
            actor = _text(message.get("from_user_id"), maximum=512)
            if message.get("group_id"):
                raise InvalidInput("WeChat group input is not part of the retained direct-message contract")
            items = message.get("item_list")
            if not isinstance(items, list) or len(items) > 64:
                raise InvalidInput("WeChat item list exceeds its bound")
            parts = []
            unavailable_media = False
            for item in items:
                if not isinstance(item, dict):
                    raise InvalidInput("WeChat message item is invalid")
                if item.get("type") in (2, 3, 4, 5):
                    unavailable_media = True
                    continue
                if item.get("type") != 1 or not isinstance(item.get("text_item"), dict):
                    raise InvalidInput("WeChat message item kind is unsupported")
                parts.append(_text(item["text_item"].get("text"), maximum=262144))
            if not parts and unavailable_media:
                logger.warning("WeChat media-only input is not accepted by the direct-text adapter")
                continue
            if unavailable_media:
                parts.append("[Media attachment unavailable in this Channel's direct-text input.]")
            text = "\n".join(parts)
            if not text or len(text.encode()) > 262144:
                raise InvalidInput("WeChat text exceeds its bound")
            event = message.get("message_id")
            if type(event) is not int or event < 0:
                raise InvalidInput("WeChat message identity is invalid")
            context = ReplyContext(provider="wechat", conversation_id=actor,
                reply_token=SecretStr(_text(message.get("context_token"), maximum=16384)))
            accepted.append(InboundResult(message=IncomingMessage(_text(str(event), maximum=512), actor, actor, None, text, None),
                private_context=context, context_expires_at=now + timedelta(hours=24)))
        return PollBatch(tuple(accepted), SecretStr(next_cursor))

    async def listen(self, channel: ChannelView, credential: Secret, on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        _configuration(channel, credential)
        if channel.id in self._listening or len(self._listening) >= 100:
            raise InvalidInput("WeChat listener is already owned or capacity is full")
        self._listening.add(channel.id)
        cursor = SecretStr("")
        try:
            while True:
                batch = await self.poll_once(channel, credential, cursor=cursor, now=datetime.now(UTC))
                for message in batch.messages:
                    await on_message(message)
                cursor = batch.next_cursor
                await asyncio.sleep(.01)
        finally:
            self._listening.discard(channel.id)

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str, content: DeliveryContent,
                   delivery_key: str, reply_context: ReplyContext | None = None, reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if content.attachments or not content.text or len(content.text.encode()) > 262144:
            return SendOutcome("failed", error="wechat_text_or_media_boundary")
        try:
            settings, token, route = _configuration(channel, credential)
            _text(destination, maximum=512)
            if reply_context is None or reply_context.provider != "wechat" or reply_context.conversation_id != destination or reply_context.reply_token is None:
                raise InvalidInput("WeChat reply context is unavailable")
        except (InvalidInput, AccessDenied):
            return SendOutcome("failed", error="wechat_configuration_or_context_unavailable")
        chunks = tuple(content.text[offset:offset + 2000] for offset in range(0, len(content.text), 2000))
        if len(chunks) > 132:
            return SendOutcome("failed", error="wechat_chunk_bound")
        completed = 0
        try:
            async with asyncio.timeout(self.timeout):
                for index, chunk in enumerate(chunks):
                    client_id = "clawith:" + hashlib.sha256(f"{channel.id}\0{delivery_key}\0{index}".encode()).hexdigest()
                    data = await self._request("POST", settings.base_url.rstrip("/") + "/ilink/bot/sendmessage",
                        headers=_headers(token=token, route=route, version=settings.channel_version),
                        payload={"msg": {"from_user_id": "", "to_user_id": destination, "client_id": client_id,
                            "message_type": 2, "message_state": 2, "context_token": reply_context.reply_token.get_secret_value(),
                            "item_list": [{"type": 1, "text_item": {"text": chunk}}]},
                            "base_info": {"channel_version": settings.channel_version, "bot_agent": "Clawith/1"}})
                    _success(data)
                    completed += 1
            return SendOutcome("delivered", acknowledgement=f"accepted_chunks:{completed}")
        except (WeChatSessionExpired, _Rejected):
            return SendOutcome("uncertain" if completed else "failed", error="wechat_session_or_send_rejected")
        except (InvalidInput, httpx.HTTPError, TimeoutError):
            return SendOutcome("uncertain", error="wechat_send_not_confirmed")
