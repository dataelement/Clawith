"""Discord signed interactions and Bot HTTP message delivery."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import SecretStr, ValidationError

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
from app.modules.channel.adapters import _json, _text
from app.modules.channel.chunks import send_chunks
from app.modules.channel.contracts import (
    ChannelView,
    DeliveryContent,
    InboundResult,
    IncomingMessage,
    Provider,
    SendOutcome,
    WebhookReply,
)
from app.modules.channel.providers import discord_gateway
from app.modules.channel.reply_context import ReplyContext
from app.modules.channel.settings import DiscordSettings
from app.modules.channel.transport import protect_request_url
from app.modules.credential.public import Secret


def _token(secret: Secret) -> str:
    data = _json(secret.value)
    if set(data) != {"version", "bot_token"} or type(data["version"]) is not int or data["version"] != 1:
        raise InvalidInput("Discord Credential bundle is invalid")
    return _text(data["bot_token"], maximum=8192)


def _snowflake(value: object) -> str:
    value = _text(value, maximum=20)
    if not value.isascii() or not value.isdigit():
        raise InvalidInput("Discord identity is invalid")
    return value


class DiscordAdapter:
    provider: Provider = "discord"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10,
            connector: discord_gateway.Connector = discord_gateway.connector) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("Discord HTTP deadline is invalid")
        self._http, self._timeout = http, timeout_seconds
        self._connector = connector

    async def listen(self, channel: ChannelView, credential: Secret,
            on_message: Callable[[InboundResult], Awaitable[None]]) -> None:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        try:
            settings = DiscordSettings.model_validate_json(channel.settings_json)
        except ValidationError:
            raise InvalidInput("Discord settings are invalid") from None
        if settings.connection_mode != "gateway":
            raise InvalidInput("Discord Gateway is not configured")
        await discord_gateway.listen(channel, _token(credential), on_message, connect_socket=self._connector)

    async def register_commands(self, channel: ChannelView, credential: Secret) -> None:
        """Replace this application's global command set with its supported /ask command."""
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        application, token = _snowflake(channel.external_identity), _token(credential)
        require_stateless_http_client(self._http)
        request = httpx.Request("PUT", f"https://discord.com/api/v10/applications/{application}/commands",
            headers={"Authorization": "Bot " + token}, json=[{"name":"ask",
                "description":"Ask the AI agent a question", "options":[{"name":"message",
                    "description":"Your question or message to the agent", "type":3,"required":True}]}],
            extensions={"timeout": httpx.Timeout(self._timeout).as_dict()})
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("Discord command registration was not confirmed")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 65536:
                        raise InvalidInput("Discord registration response exceeds its bound")
                    raw.extend(chunk)
                commands = _json(b'{"commands":' + bytes(raw) + b'}')["commands"]
                if not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict) or commands[0].get("name") != "ask" or commands[0].get("application_id") != application:
                    raise InvalidInput("Discord command registration acknowledgement is invalid")
            finally:
                await response.aclose()

    async def send_file(self, channel: ChannelView, credential: Secret, *, destination: str,
            filename: str, content: bytes, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if channel.provider != self.provider or not channel.enabled:
            return SendOutcome("failed", error="channel_unavailable")
        if not content or len(content) > 10 * 1024 * 1024:
            return SendOutcome("failed", error="discord_file_exceeds_upload_bound")
        try:
            _text(filename)
            request = self._message_request(channel, credential, destination=destination,
                reply_context=reply_context, reply_operation=reply_operation,
                filename=filename, content=content)
        except InvalidInput:
            return SendOutcome("failed", error="invalid_discord_configuration")
        return await self._send_request(request, destination=destination)

    async def download_resource(self, channel: ChannelView, credential: Secret, *, reference: str,
            maximum: int = 10 * 1024 * 1024) -> bytes:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        if type(maximum) is not int or not 1 <= maximum <= 10 * 1024 * 1024:
            raise InvalidInput("Discord download bound is invalid")
        parts = reference.split("/")
        if len(parts) != 3:
            raise InvalidInput("Discord resource reference is invalid")
        conversation, message_id, attachment_id = (_snowflake(part) for part in parts)
        token = _token(credential)
        status, message = await self._request_json(httpx.Request("GET",
            f"https://discord.com/api/v10/channels/{conversation}/messages/{message_id}",
            headers={"Authorization":"Bot " + token}))
        attachments = message.get("attachments")
        if status != 200 or message.get("channel_id") != conversation or message.get("id") != message_id or not isinstance(attachments, list) or len(attachments) > 64:
            raise InvalidInput("Discord resource message is unavailable")
        matching = [item for item in attachments if isinstance(item, dict) and item.get("id") == attachment_id]
        if len(matching) != 1:
            raise InvalidInput("Discord attachment is unavailable")
        url = _text(matching[0].get("url"), maximum=8192)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"cdn.discordapp.com", "media.discordapp.net"} or parsed.username or parsed.password:
            raise AccessDenied("Discord attachment endpoint is invalid")
        request = protect_request_url(httpx.Request("GET", url,
            extensions={"timeout":httpx.Timeout(self._timeout).as_dict()}))
        require_stateless_http_client(self._http)
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("Discord attachment could not be downloaded")
                output = bytearray()
                async for part in response.aiter_bytes():
                    if len(output) + len(part) > maximum:
                        raise InvalidInput("Discord attachment exceeds its bound")
                    output.extend(part)
                return bytes(output)
            finally:
                await response.aclose()

    def _message_request(self, channel: ChannelView, credential: Secret, *, destination: str,
            reply_context: ReplyContext | None, reply_operation: Literal["original", "followup"] | None,
            text: str = "", filename: str | None = None, content: bytes | None = None) -> httpx.Request:
        token = _token(credential)
        destination, application = _snowflake(destination), _snowflake(channel.external_identity)
        if reply_context is not None and (reply_context.provider != "discord" or reply_context.conversation_id != destination or reply_context.reply_token is None):
            raise InvalidInput("Discord reply context does not match")
        if (reply_context is None) != (reply_operation is None):
            raise InvalidInput("Discord reply operation requires its captured context")
        endpoint = f"https://discord.com/api/v10/channels/{destination}/messages"
        if reply_context is not None and reply_context.reply_token is not None:
            endpoint = f"https://discord.com/api/v10/webhooks/{application}/{quote(reply_context.reply_token.get_secret_value(), safe='')}"
            endpoint += "/messages/@original" if reply_operation == "original" else "?wait=true"
        method = "PATCH" if reply_operation == "original" else "POST"
        headers = {} if reply_context else {"Authorization":"Bot " + token}
        if content is None:
            request = httpx.Request(method, endpoint, headers=headers,
                json={"content":text,"allowed_mentions":{"parse":[]}})
        else:
            import json
            request = httpx.Request(method, endpoint, headers=headers,
                data={"payload_json":json.dumps({"attachments":[{"id":0,"filename":filename}],
                    "allowed_mentions":{"parse":[]}})}, files={"files[0]":(filename,content,"application/octet-stream")})
        request.extensions["timeout"] = httpx.Timeout(self._timeout).as_dict()
        if reply_context is not None:
            protect_request_url(request)
        return request

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> InboundResult:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        if len(body) > 262144 or now.tzinfo is None:
            raise InvalidInput("Discord request exceeds its supported boundary")
        try:
            settings = DiscordSettings.model_validate_json(channel.settings_json)
        except ValidationError:
            raise InvalidInput("Discord settings are invalid") from None
        _token(credential)
        if settings.connection_mode != "webhook" or settings.public_key is None:
            raise AccessDenied("Discord webhook is not configured")
        headers = {key.lower(): value for key, value in headers.items()}
        stamp = headers.get("x-signature-timestamp", "")
        if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 16 or abs(now.timestamp() - int(stamp)) > 300:
            raise AccessDenied("Discord timestamp is invalid")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(settings.public_key)).verify(
                bytes.fromhex(headers.get("x-signature-ed25519", "")), stamp.encode() + body)
        except (ValueError, InvalidSignature):
            raise AccessDenied("Discord signature is invalid") from None
        payload = _json(body)
        if _snowflake(payload.get("application_id")) != channel.external_identity:
            raise AccessDenied("Discord application does not match")
        if payload.get("type") == 1:
            return InboundResult(reply=WebhookReply(200, "application/json", '{"type":1}'))
        if payload.get("type") != 2:
            raise InvalidInput("Discord interaction type is unsupported")
        command = payload.get("data")
        if not isinstance(command, dict) or command.get("name") != "ask":
            raise InvalidInput("Discord command is unsupported")
        options = command.get("options")
        if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], dict) or options[0].get("name") != "message" or options[0].get("type") != 3:
            raise InvalidInput("Discord command options are invalid")
        text = _text(options[0].get("value"), maximum=262144)
        member = payload.get("member")
        user = member.get("user") if isinstance(member, dict) else payload.get("user")
        if not isinstance(user, dict) or user.get("bot"):
            raise AccessDenied("Discord interaction requires a human actor")
        conversation = _snowflake(payload.get("channel_id"))
        event_id = _snowflake(payload.get("id"))
        context = ReplyContext(provider="discord", conversation_id=conversation,
            reply_token=SecretStr(_text(payload.get("token"), maximum=16384)))
        return InboundResult(message=IncomingMessage(event_id, _snowflake(user.get("id")),
            conversation, conversation if payload.get("guild_id") else None, text, None),
            reply=WebhookReply(200, "application/json", '{"type":5}'),
            private_context=context, context_expires_at=now + timedelta(minutes=15))

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if content.attachments:
            return SendOutcome("failed", error="attachment_delivery_not_implemented")
        async def send(text: str, index: int) -> SendOutcome:
            operation = "followup" if index > 0 and reply_context is not None else reply_operation
            return await self._send_one(channel, credential, destination=destination,
                content=DeliveryContent(text), delivery_key=delivery_key,
                reply_context=reply_context, reply_operation=operation)
        return await send_chunks(content.text, max_characters=2000, max_bytes=8000, send=send)

    async def _send_one(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if channel.provider != self.provider or not channel.enabled:
            return SendOutcome("failed", error="channel_unavailable")
        try:
            request = self._message_request(channel, credential, destination=destination,
                reply_context=reply_context, reply_operation=reply_operation, text=content.text)
        except InvalidInput:
            return SendOutcome("failed", error="invalid_discord_configuration")
        return await self._send_request(request, destination=destination)

    async def _send_request(self, request: httpx.Request, *, destination: str) -> SendOutcome:
        try:
            status, payload = await self._request_json(request)
            if 400 <= status < 500:
                return SendOutcome("failed", error=f"discord_http_{status}")
            if status != 200:
                return SendOutcome("uncertain", error="discord_send_not_confirmed")
            if payload.get("channel_id") != destination:
                return SendOutcome("uncertain", error="discord_acknowledgement_invalid")
            identity = _snowflake(payload.get("id"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except (httpx.HTTPError, TimeoutError, InvalidInput):
            return SendOutcome("uncertain", error="discord_send_not_confirmed")

    async def _request_json(self, request: httpx.Request) -> tuple[int, dict]:
        require_stateless_http_client(self._http)
        request.extensions["timeout"] = httpx.Timeout(self._timeout).as_dict()
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    return response.status_code, {}
                raw = bytearray()
                async for part in response.aiter_bytes():
                    if len(raw) + len(part) > 262144:
                        raise InvalidInput("Discord response exceeds its bound")
                    raw.extend(part)
                return response.status_code, _json(bytes(raw))
            finally:
                await response.aclose()
