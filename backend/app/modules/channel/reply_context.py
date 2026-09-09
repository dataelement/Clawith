"""Channel-only encryption for authenticated provider reply coordinates."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from app.infrastructure.errors import InvalidInput


class MediaContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    reference_id: str = Field(min_length=1, max_length=512)
    download_url: SecretStr
    aes_key: SecretStr
    name: str = Field(min_length=1, max_length=512)
    media_type: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_private_fields(self) -> "MediaContext":
        from urllib.parse import urlsplit
        parsed = urlsplit(self.download_url.get_secret_value())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Media URL is invalid")
        if len(self.download_url.get_secret_value().encode()) > 8192 or not 1 <= len(self.aes_key.get_secret_value().encode()) <= 256:
            raise ValueError("Media coordinates exceed their bound")
        return self


class ReplyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    provider: Literal["discord", "teams", "wechat", "wecom"]
    conversation_id: str = Field(min_length=1, max_length=512)
    reply_token: SecretStr | None = None
    service_url: str | None = Field(default=None, max_length=2048)
    media: tuple[MediaContext, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "ReplyContext":
        if self.provider == "wecom":
            if self.reply_token is not None or self.service_url is not None or not self.media:
                raise ValueError("WeCom media context requires private media coordinates")
        elif self.media:
            raise ValueError("Media coordinates are not supported for this provider")
        if self.provider == "teams":
            if self.reply_token is not None or not self.service_url:
                raise ValueError("Teams reply requires a signed service URL")
            from urllib.parse import urlsplit
            parsed = urlsplit(self.service_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Teams service URL is invalid")
        elif self.provider != "wecom" and (self.service_url is not None or self.reply_token is None):
            raise ValueError("Provider reply token is required")
        if self.reply_token is not None:
            token = self.reply_token.get_secret_value()
            if not token or len(token.encode()) > 16384:
                raise ValueError("Provider reply token exceeds its bound")
        if len(self.conversation_id.encode()) > 512:
            raise ValueError("Provider conversation exceeds its bound")
        return self


@dataclass(frozen=True, slots=True)
class ContextScope:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    channel_configuration_id: UUID
    external_event_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SealedContext:
    context_version: int
    key_version: str
    nonce: bytes
    ciphertext: bytes


def _aad(scope: ContextScope, key_version: str) -> bytes:
    try:
        if scope.expires_at.tzinfo is None or not scope.external_event_id or len(scope.external_event_id) > 512 or len(scope.external_event_id.encode()) > 512:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise InvalidInput("Channel reply context scope is invalid") from None
    return json.dumps(["clawith:channel:reply-context", 1, key_version, str(scope.id),
        str(scope.tenant_id), str(scope.agent_id), str(scope.channel_configuration_id),
        scope.external_event_id, scope.expires_at.timestamp()], separators=(",", ":")).encode()


class ChannelContextCodec:
    def __init__(self, *, active_key_version: str, keys: Mapping[str, bytes]) -> None:
        if not active_key_version or active_key_version not in keys:
            raise InvalidInput("Channel encryption key is unavailable")
        self._keys: dict[str, bytes] = {}
        self._sync_keys: dict[str, bytes] = {}
        for version, key in keys.items():
            if not version or len(version) > 64 or len(key) != 32:
                raise InvalidInput("Channel encryption key is invalid")
            self._keys[version] = HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"clawith:channel:v1", info=b"reply-context:aes-gcm").derive(key)
            self._sync_keys[version] = HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"clawith:channel:v1", info=b"sync-cursor:aes-gcm").derive(key)
        self._active = active_key_version

    def _seal_sync(self, payload: bytes, aad: bytes) -> SealedContext:
        if len(payload) > 65520:
            raise InvalidInput("Channel synchronization coordinate exceeds its bound")
        nonce = os.urandom(12)
        return SealedContext(1, self._active, nonce,
            AESGCM(self._sync_keys[self._active]).encrypt(nonce, payload, aad + self._active.encode()))

    def _open_sync(self, sealed: SealedContext, aad: bytes) -> bytes:
        key = self._sync_keys.get(sealed.key_version)
        if key is None or sealed.context_version != 1 or len(sealed.nonce) != 12 or not 16 < len(sealed.ciphertext) <= 65536:
            raise InvalidInput("Channel synchronization encoding is unsupported")
        try:
            return AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, aad + sealed.key_version.encode())
        except InvalidTag:
            raise InvalidInput("Channel synchronization coordinate authentication failed") from None

    def seal(self, context: ReplyContext, *, scope: ContextScope) -> SealedContext:
        try:
            # Explicit v1 fields do not change when the public type later evolves.
            document = {"provider": context.provider, "conversation_id": context.conversation_id,
                "reply_token": context.reply_token.get_secret_value() if context.reply_token else None,
                "service_url": context.service_url,
                "media": [{"reference_id": item.reference_id, "download_url": item.download_url.get_secret_value(),
                    "aes_key": item.aes_key.get_secret_value(), "name": item.name, "media_type": item.media_type}
                    for item in context.media]}
            raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
            if len(raw) > 65520:
                raise ValueError()
            ReplyContext.model_validate_json(raw)
        except (ValueError, UnicodeError, ValidationError):
            raise InvalidInput("Channel reply context is invalid") from None
        nonce = os.urandom(12)
        return SealedContext(1, self._active, nonce,
            AESGCM(self._keys[self._active]).encrypt(nonce, raw, _aad(scope, self._active)))

    def open(self, sealed: SealedContext, *, scope: ContextScope, now: datetime) -> ReplyContext:
        if now.tzinfo is None or scope.expires_at.tzinfo is None or now >= scope.expires_at:
            raise InvalidInput("Channel reply context has expired")
        key = self._keys.get(sealed.key_version)
        if sealed.context_version != 1 or key is None or len(sealed.nonce) != 12 or not 16 < len(sealed.ciphertext) <= 65536:
            raise InvalidInput("Channel reply context version or encoding is invalid")
        try:
            raw = AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, _aad(scope, sealed.key_version))
            return ReplyContext.model_validate_json(raw)
        except (InvalidTag, ValueError, ValidationError):
            raise InvalidInput("Channel reply context authentication or shape is invalid") from None
