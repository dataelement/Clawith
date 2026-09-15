"""Slack HTTP protocol adapter; other provider transports are not implemented here."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import require_stateless_http_client
from app.modules.channel.chunks import send_chunks
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
from app.modules.channel.transport import protect_request_url
from app.modules.credential.public import Secret


def _text(value: object, *, maximum: int = 512, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise InvalidInput("Channel text or identity exceeds its supported bound")
    try:
        if len(value.encode()) > maximum:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise InvalidInput("Channel text or identity exceeds its supported bound") from None
    return value


def _json(raw: str | bytes) -> dict:
    if len(raw) > 262144:
        raise InvalidInput("Channel payload exceeds its byte bound")
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("Channel payload is invalid") from None
    if not isinstance(value, dict):
        raise InvalidInput("Channel payload must be an object")
    return value


def _slack_secrets(credential: Secret) -> tuple[str, str]:
    data = _json(credential.value)
    if set(data) != {"version", "token", "signing_secret"} or type(data["version"]) is not int or data["version"] != 1:
        raise InvalidInput("Slack Credential bundle version or fields are invalid")
    return _text(data["token"], maximum=8192), _text(data["signing_secret"], maximum=8192)


class _SlackRejected(InvalidInput):
    pass


class SlackAdapter:
    provider: Provider = "slack"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("Channel HTTP deadline is invalid")
        self._http, self._timeout = http, timeout_seconds

    async def _request_json(self, request: httpx.Request) -> dict:
        require_stateless_http_client(self._http)
        request.extensions["timeout"] = httpx.Timeout(self._timeout).as_dict()
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if 400 <= response.status_code < 500:
                    raise _SlackRejected("Slack rejected the operation")
                if response.status_code != 200:
                    raise InvalidInput("Slack operation was not confirmed")
                raw = bytearray()
                async for part in response.aiter_bytes():
                    if len(raw) + len(part) > 262144:
                        raise InvalidInput("Slack response exceeds its bound")
                    raw.extend(part)
                data = _json(bytes(raw))
                if data.get("ok") is False:
                    raise _SlackRejected("Slack rejected the operation")
                if data.get("ok") is not True:
                    raise InvalidInput("Slack acknowledgement is invalid")
                return data
            finally:
                await response.aclose()

    async def download_file(self, channel: ChannelView, credential: Secret, *, file_id: str,
            maximum: int = 16 * 1024 * 1024) -> bytes:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        if type(maximum) is not int or not 1 <= maximum <= 16 * 1024 * 1024:
            raise InvalidInput("Slack file download bound is invalid")
        token, _ = _slack_secrets(credential)
        data = await self._request_json(httpx.Request("GET", "https://slack.com/api/files.info",
            params={"file":_text(file_id)}, headers={"Authorization":"Bearer " + token}))
        file = data.get("file")
        if not isinstance(file, dict) or file.get("id") != file_id:
            raise InvalidInput("Slack file identity is invalid")
        url = _text(file.get("url_private_download"), maximum=8192)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "files.slack.com" or parsed.username or parsed.password:
            raise AccessDenied("Slack file download endpoint is invalid")
        request = protect_request_url(httpx.Request("GET", url, headers={"Authorization":"Bearer " + token},
            extensions={"timeout":httpx.Timeout(self._timeout).as_dict()}))
        require_stateless_http_client(self._http)
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise InvalidInput("Slack file could not be downloaded")
                output = bytearray()
                async for part in response.aiter_bytes():
                    if len(output) + len(part) > maximum:
                        raise InvalidInput("Slack file exceeds its download bound")
                    output.extend(part)
                return bytes(output)
            finally:
                await response.aclose()

    async def send_file(self, channel: ChannelView, credential: Secret, *, destination: str,
            filename: str, content: bytes) -> SendOutcome:
        if channel.provider != self.provider or not channel.enabled:
            return SendOutcome("failed", error="channel_unavailable")
        if not content or len(content) > 16 * 1024 * 1024:
            return SendOutcome("failed", error="slack_file_exceeds_upload_bound")
        finalizing = False
        try:
            token, _ = _slack_secrets(credential)
            _text(destination)
            _text(filename)
            data = await self._request_json(httpx.Request("GET", "https://slack.com/api/files.getUploadURLExternal",
                params={"filename":filename,"length":str(len(content))}, headers={"Authorization":"Bearer " + token}))
            file_id = _text(data.get("file_id"))
            upload_url = _text(data.get("upload_url"), maximum=8192)
            parsed = urlsplit(upload_url)
            if parsed.scheme != "https" or parsed.hostname != "files.slack.com" or parsed.username or parsed.password:
                raise InvalidInput("Slack upload endpoint is invalid")
            request = protect_request_url(httpx.Request("POST", upload_url, content=content,
                extensions={"timeout":httpx.Timeout(self._timeout).as_dict()}))
            require_stateless_http_client(self._http)
            async with asyncio.timeout(self._timeout):
                response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise InvalidInput("Slack file upload was not confirmed")
                finally:
                    await response.aclose()
            finalizing = True
            result = await self._request_json(httpx.Request("POST", "https://slack.com/api/files.completeUploadExternal",
                headers={"Authorization":"Bearer " + token},
                json={"files":[{"id":file_id,"title":filename}],"channel_id":destination}))
            files = result.get("files")
            if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict) or files[0].get("id") != file_id:
                raise InvalidInput("Slack file publication acknowledgement is invalid")
            return SendOutcome("delivered", acknowledgement=file_id)
        except _SlackRejected:
            return SendOutcome("failed", error="slack_file_rejected")
        except (httpx.HTTPError, TimeoutError, InvalidInput):
            return SendOutcome("uncertain" if finalizing else "failed", error="slack_file_not_confirmed")

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> InboundResult:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        if len(body) > 262144 or now.tzinfo is None:
            raise InvalidInput("Channel request exceeds its supported boundary")
        _, signing = _slack_secrets(credential)
        normalized = {key.lower(): value for key, value in headers.items()}
        stamp = normalized.get("x-slack-request-timestamp", "")
        signature = normalized.get("x-slack-signature", "")
        if not stamp.isdigit() or len(stamp) > 16 or abs(now.timestamp() - int(stamp)) > 300:
            raise AccessDenied("Slack request timestamp is invalid")
        expected = "v0=" + hmac.new(signing.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise AccessDenied("Slack request signature is invalid")
        data = _json(body)
        if data.get("type") == "url_verification":
            return InboundResult(challenge=_text(data.get("challenge"), maximum=4096))
        if data.get("type") != "event_callback":
            return InboundResult()
        identity = _text(data.get("team_id"), maximum=255) + ":" + _text(data.get("api_app_id"), maximum=255)
        if identity != channel.external_identity:
            raise AccessDenied("Slack event belongs to another workspace")
        event = data.get("event")
        if not isinstance(event, dict):
            raise InvalidInput("Slack event is invalid")
        if event.get("bot_id") or event.get("subtype") not in (None, "file_share") or event.get("type") not in {"message", "app_mention"}:
            return InboundResult()
        conversation = _text(event.get("channel"))
        if not conversation.startswith("D") and event.get("type") != "app_mention":
            return InboundResult()
        files = event.get("files", [])
        if not isinstance(files, list) or len(files) > 64:
            raise InvalidInput("Slack attachments exceed their bound")
        references = []
        for item in files:
            if not isinstance(item, dict):
                raise InvalidInput("Slack attachment is invalid")
            references.append(AttachmentReference(_text(item.get("id")), _text(item.get("name")),
                _text(item["mimetype"], maximum=256) if "mimetype" in item else None))
        return InboundResult(message=IncomingMessage(_text(data.get("event_id")), _text(event.get("user")),
            conversation, None if conversation.startswith("D") else conversation,
            _text(event.get("text", ""), maximum=262144, empty=True),
            _text(event["thread_ts"]) if "thread_ts" in event else None, tuple(references)))

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if content.attachments:
            return SendOutcome("failed", error="attachment_delivery_not_implemented")
        async def send(text: str, index: int) -> SendOutcome:
            return await self._send_one(channel, credential, destination=destination,
                content=DeliveryContent(text), delivery_key=delivery_key)
        return await send_chunks(content.text, max_characters=4000, max_bytes=16000, send=send)

    async def _send_one(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str) -> SendOutcome:
        if channel.provider != self.provider or not channel.enabled:
            return SendOutcome("failed", error="channel_unavailable")
        try:
            token, _ = _slack_secrets(credential)
            _text(destination)
        except InvalidInput:
            return SendOutcome("failed", error="invalid_slack_configuration")
        require_stateless_http_client(self._http)
        request = httpx.Request("POST", "https://slack.com/api/chat.postMessage",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json={"channel": destination, "text": content.text},
            extensions={"timeout": httpx.Timeout(self._timeout).as_dict()})
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    if response.status_code in {400, 401, 403, 404, 422, 429}:
                        return SendOutcome("failed", error=f"slack_http_{response.status_code}")
                    if response.status_code != 200:
                        return SendOutcome("uncertain", error="slack_send_not_confirmed")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 262144:
                            return SendOutcome("uncertain", error="slack_response_too_large")
                        raw.extend(chunk)
                    data = _json(bytes(raw))
                    if data.get("ok") is False:
                        return SendOutcome("failed", error="slack_rejected_message")
                    if data.get("ok") is not True or data.get("channel") != destination:
                        return SendOutcome("uncertain", error="slack_acknowledgement_invalid")
                    identity = _text(data.get("ts"))
                    return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
                finally:
                    await response.aclose()
        except (httpx.HTTPError, TimeoutError, InvalidInput):
            return SendOutcome("uncertain", error="slack_send_not_confirmed")
