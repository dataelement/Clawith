"""Bot Connector JWT authentication and source-bound HTTPS activity replies."""

import asyncio
import base64
import hmac
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from azure.identity.aio import ManagedIdentityCredential
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import ValidationError

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
)
from app.modules.channel.reply_context import ReplyContext
from app.modules.channel.settings import TeamsSettings
from app.modules.credential.public import Secret


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError:
        raise AccessDenied("Teams JWT encoding is invalid") from None


def _secret(credential: Secret) -> tuple[Literal["client_secret", "managed_identity"], str]:
    payload = _json(credential.value)
    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise InvalidInput("Teams Credential bundle is invalid")
    if set(payload) == {"version", "client_secret"}:
        return "client_secret", _text(payload["client_secret"], maximum=16384)
    if set(payload) == {"version", "managed_identity_client_id"}:
        return "managed_identity", _text(payload["managed_identity_client_id"], maximum=256)
    raise InvalidInput("Teams Credential bundle is invalid")


def _managed_identity(client_id: str) -> AsyncTokenCredential:
    return ManagedIdentityCredential(client_id=client_id)


class TeamsAdapter:
    provider: Provider = "teams"

    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 10,
            managed_identity_factory: Callable[[str], AsyncTokenCredential] = _managed_identity) -> None:
        require_stateless_http_client(http)
        if not 0 < timeout_seconds <= 120:
            raise InvalidInput("Teams HTTP deadline is invalid")
        self._http, self._timeout = http, timeout_seconds
        self._managed_identity_factory = managed_identity_factory

    async def _request(self, request: httpx.Request) -> tuple[int, dict]:
        require_stateless_http_client(self._http)
        request.extensions["timeout"] = httpx.Timeout(self._timeout).as_dict()
        async with asyncio.timeout(self._timeout):
            response = await self._http.send(request, auth=None, follow_redirects=False, stream=True)
            try:
                if response.status_code not in (200, 201):
                    return response.status_code, {}
                raw = bytearray()
                async for part in response.aiter_bytes():
                    if len(raw) + len(part) > 262144:
                        raise InvalidInput("Teams response exceeds its bound")
                    raw.extend(part)
                return response.status_code, _json(bytes(raw))
            finally:
                await response.aclose()

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
            headers: dict[str, str], now: datetime) -> InboundResult:
        if channel.provider != self.provider or not channel.enabled:
            raise AccessDenied("Channel is unavailable")
        if now.tzinfo is None or len(body) > 262144:
            raise InvalidInput("Teams request exceeds its boundary")
        payload = _json(body)
        authorization = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 32768:
            raise AccessDenied("Teams authorization is invalid")
        parts = authorization[7:].split(".")
        if len(parts) != 3:
            raise AccessDenied("Teams JWT encoding is invalid")
        header, claims = _json(_decode(parts[0])), _json(_decode(parts[1]))
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AccessDenied("Teams JWT algorithm is invalid")
        status, keyset = await self._request(httpx.Request("GET", "https://login.botframework.com/v1/.well-known/keys"))
        keys = keyset.get("keys")
        if status != 200 or not isinstance(keys, list) or len(keys) > 64:
            raise AccessDenied("Teams signing keys are unavailable")
        candidates = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]]
        if len(candidates) != 1 or candidates[0].get("kty") != "RSA":
            raise AccessDenied("Teams signing key is unavailable")
        key = candidates[0]
        try:
            modulus = _decode(_text(key.get("n"), maximum=2048))
            exponent = _decode(_text(key.get("e"), maximum=16))
            if not 256 <= len(modulus) <= 1024:
                raise ValueError()
            rsa.RSAPublicNumbers(int.from_bytes(exponent), int.from_bytes(modulus)).public_key().verify(
                _decode(parts[2]), (parts[0] + "." + parts[1]).encode(), padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, InvalidSignature):
            raise AccessDenied("Teams JWT signature is invalid") from None
        exp, nbf = claims.get("exp"), claims.get("nbf")
        if type(exp) is not int or type(nbf) is not int or not nbf <= now.timestamp() < exp:
            raise AccessDenied("Teams JWT has expired or is not active")
        if claims.get("aud") != channel.external_identity or claims.get("iss") != "https://api.botframework.com":
            raise AccessDenied("Teams JWT audience or issuer is invalid")
        service_url = _text(payload.get("serviceUrl"), maximum=2048)
        signed_url = claims.get("serviceurl")
        if not isinstance(signed_url, str) or not hmac.compare_digest(signed_url.encode(), service_url.encode()):
            raise AccessDenied("Teams service URL is not authenticated")
        if payload.get("type") != "message":
            return InboundResult()
        conversation, sender = payload.get("conversation"), payload.get("from")
        if not isinstance(conversation, dict) or not isinstance(sender, dict):
            raise InvalidInput("Teams message identity is invalid")
        conversation_id = _text(conversation.get("id"))
        try:
            context = ReplyContext(provider="teams", conversation_id=conversation_id, service_url=service_url)
        except ValidationError:
            raise AccessDenied("Teams service URL is invalid") from None
        if payload.get("attachments"):
            raise InvalidInput("Teams attachment materialization is not yet available")
        return InboundResult(message=IncomingMessage(_text(payload.get("id")), _text(sender.get("id")),
            conversation_id, conversation_id if conversation.get("conversationType") != "personal" else None,
            _text(payload.get("text", ""), maximum=262144, empty=True),
            _text(payload["replyToId"]) if "replyToId" in payload else None), private_context=context,
            context_expires_at=datetime.fromtimestamp(exp, tz=now.tzinfo))

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
            content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
            reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome:
        if content.attachments:
            return SendOutcome("failed", error="attachment_delivery_not_implemented")
        if reply_context is None or reply_context.provider != "teams" or reply_context.conversation_id != destination or not reply_context.service_url:
            return SendOutcome("failed", error="teams_authenticated_reply_context_required")
        service_url = reply_context.service_url
        access_token: str | None = None
        async def send(text: str, index: int) -> SendOutcome:
            nonlocal access_token
            if access_token is None:
                prepared = await self._prepare_token(channel, credential)
                if isinstance(prepared, SendOutcome):
                    return prepared
                access_token = prepared
            return await self._send_one(access_token, destination=destination,
                text=text, service_url=service_url)
        return await send_chunks(content.text, max_characters=28000, max_bytes=28000, send=send)

    async def _prepare_token(self, channel: ChannelView, credential: Secret) -> str | SendOutcome:
        if channel.provider != self.provider or not channel.enabled:
            return SendOutcome("failed", error="channel_unavailable")
        try:
            settings = TeamsSettings.model_validate_json(channel.settings_json)
            authentication, secret = _secret(credential)
        except (ValidationError, InvalidInput):
            return SendOutcome("failed", error="invalid_teams_configuration")
        if authentication == "managed_identity":
            identity = self._managed_identity_factory(secret)
            try:
                async with asyncio.timeout(self._timeout):
                    access = await identity.get_token("https://api.botframework.com/.default")
                    return _text(access.token, maximum=16384)
            except (AzureError, TimeoutError, InvalidInput):
                return SendOutcome("failed", error="teams_managed_identity_unavailable")
            finally:
                await identity.close()
        try:
            status, token = await self._request(httpx.Request("POST",
                f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token", data={
                    "grant_type":"client_credentials", "client_id":channel.external_identity,
                    "client_secret":secret,"scope":"https://api.botframework.com/.default"}))
            if status != 200:
                return SendOutcome("failed", error="teams_token_request_rejected")
            return _text(token.get("access_token"), maximum=16384)
        except (httpx.HTTPError, TimeoutError, InvalidInput):
            return SendOutcome("failed", error="teams_token_unavailable")

    async def _send_one(self, access_token: str, *, destination: str, text: str, service_url: str) -> SendOutcome:
        try:
            status, result = await self._request(httpx.Request("POST",
                service_url.rstrip("/") + "/v3/conversations/" + quote(destination, safe="") + "/activities",
                headers={"Authorization":"Bearer " + access_token}, json={"type":"message", "text":text}))
            if 400 <= status < 500:
                return SendOutcome("failed", error=f"teams_http_{status}")
            if status not in (200, 201):
                return SendOutcome("uncertain", error="teams_send_not_confirmed")
            identity = _text(result.get("id"))
            return SendOutcome("delivered", acknowledgement=identity, provider_reply_ids=(identity,))
        except (httpx.HTTPError, TimeoutError, InvalidInput):
            return SendOutcome("uncertain", error="teams_send_not_confirmed")
