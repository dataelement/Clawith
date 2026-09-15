"""Authenticated encryption for versioned Credential payloads."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.infrastructure.errors import InvalidInput

PAYLOAD_VERSION = 1
NONCE_BYTES = 12
MAX_SECRET_BYTES = 16_384


@dataclass(frozen=True, slots=True, repr=False)
class Secret:
    """Secret plaintext whose representation never contains its value."""

    value: str

    def __repr__(self) -> str:
        return "Secret(<redacted>)"


class CredentialKeyring:
    """Explicitly injected AES key versions; no key is generated implicitly."""

    def __init__(self, *, active_key_version: str, keys: Mapping[str, bytes]) -> None:
        if not active_key_version or active_key_version not in keys:
            raise InvalidInput("active Credential key version is unavailable")
        copied: dict[str, bytes] = {}
        for version, key in keys.items():
            if not version or len(version) > 64:
                raise InvalidInput("Credential key version is invalid")
            if len(key) not in (16, 24, 32):
                raise InvalidInput("Credential AES key length is invalid")
            copied[version] = bytes(key)
        self._active_key_version = active_key_version
        self._keys = MappingProxyType(copied)

    @property
    def active_key_version(self) -> str:
        return self._active_key_version

    def encrypt(self, *, credential_id: UUID, tenant_id: UUID, secret: Secret) -> tuple[bytes, int, str]:
        payload = _encode_payload(secret)
        nonce = os.urandom(NONCE_BYTES)
        aad = _aad(credential_id=credential_id, tenant_id=tenant_id, payload_version=PAYLOAD_VERSION)
        encrypted = AESGCM(self._keys[self._active_key_version]).encrypt(nonce, payload, aad)
        return nonce + encrypted, PAYLOAD_VERSION, self._active_key_version

    def decrypt(
        self,
        *,
        credential_id: UUID,
        tenant_id: UUID,
        encrypted_payload: bytes,
        payload_version: int,
        key_version: str,
    ) -> Secret:
        if payload_version != PAYLOAD_VERSION:
            raise InvalidInput("unsupported Credential payload version")
        key = self._keys.get(key_version)
        if key is None:
            raise InvalidInput("Credential encryption key is unavailable")
        if len(encrypted_payload) <= NONCE_BYTES:
            raise InvalidInput("Credential payload is invalid")
        nonce, ciphertext = encrypted_payload[:NONCE_BYTES], encrypted_payload[NONCE_BYTES:]
        aad = _aad(credential_id=credential_id, tenant_id=tenant_id, payload_version=payload_version)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag:
            raise InvalidInput("Credential payload authentication failed") from None
        return _decode_payload(plaintext)


def _encode_payload(secret: Secret) -> bytes:
    value = secret.value.encode("utf-8")
    if not value or len(value) > MAX_SECRET_BYTES:
        raise InvalidInput("Credential Secret size is invalid")
    return json.dumps(
        {"schema_version": PAYLOAD_VERSION, "value": secret.value},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_payload(payload: bytes) -> Secret:
    try:
        parsed: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidInput("Credential payload is invalid") from None
    if type(parsed) is not dict:
        raise InvalidInput("Credential payload is invalid")
    decoded = cast(dict[str, object], parsed)
    if set(decoded) != {"schema_version", "value"}:
        raise InvalidInput("Credential payload is invalid")
    if decoded["schema_version"] != PAYLOAD_VERSION or type(decoded["value"]) is not str:
        raise InvalidInput("Credential payload is invalid")
    value = decoded["value"].encode("utf-8")
    if not value or len(value) > MAX_SECRET_BYTES:
        raise InvalidInput("Credential payload is invalid")
    return Secret(decoded["value"])


def _aad(*, credential_id: UUID, tenant_id: UUID, payload_version: int) -> bytes:
    return f"clawith:credential:{payload_version}:{tenant_id}:{credential_id}".encode()
