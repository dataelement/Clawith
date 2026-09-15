"""Bounded password verification and opaque login-token mechanics."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
from typing import cast

from app.infrastructure.errors import InvalidInput

KDF_NAME = "scrypt"
KDF_VERSION = 1
MAX_PASSWORD_BYTES = 1024
MAX_LOGIN_TOKEN_LENGTH = 256
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_TOKEN_BYTES = 32


async def create_password_verifier(password: str) -> str:
    encoded = _password_bytes(password)
    salt = os.urandom(_SALT_BYTES)
    derived = await asyncio.to_thread(_derive, encoded, salt)
    payload = {
        "dk": _encode(derived),
        "n": _SCRYPT_N,
        "p": _SCRYPT_P,
        "r": _SCRYPT_R,
        "salt": _encode(salt),
        "version": KDF_VERSION,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


async def verify_login_password(password: str, encoded_verifier: str) -> bool:
    valid_input = True
    try:
        password_bytes = _password_bytes(password)
    except InvalidInput:
        password_bytes = b"invalid-password"
        valid_input = False
    try:
        salt, expected = _decode_verifier(encoded_verifier)
    except InvalidInput:
        salt = bytes(_SALT_BYTES)
        expected = bytes(_DKLEN)
        valid_input = False
    actual = await asyncio.to_thread(_derive, password_bytes, salt)
    return valid_input and hmac.compare_digest(actual, expected)


def issue_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(_TOKEN_BYTES)).rstrip(b"=").decode("ascii")


def token_digest(token: str) -> str:
    if not token or len(token) > MAX_LOGIN_TOKEN_LENGTH:
        raise InvalidInput("login token is invalid")
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError:
        raise InvalidInput("login token is invalid") from None
    return hashlib.sha256(encoded).hexdigest()


def _derive(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)


def _password_bytes(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
        raise InvalidInput("password is invalid")
    return encoded


def _decode_verifier(encoded_verifier: str) -> tuple[bytes, bytes]:
    try:
        decoded: object = json.loads(encoded_verifier)
    except (TypeError, json.JSONDecodeError):
        raise InvalidInput("password verifier is invalid") from None
    required = {"dk", "n", "p", "r", "salt", "version"}
    if type(decoded) is not dict:
        raise InvalidInput("password verifier is invalid")
    payload = cast(dict[str, object], decoded)
    if set(payload) != required:
        raise InvalidInput("password verifier is invalid")
    if (
        payload["version"] != KDF_VERSION
        or payload["n"] != _SCRYPT_N
        or payload["r"] != _SCRYPT_R
        or payload["p"] != _SCRYPT_P
    ):
        raise InvalidInput("password verifier is invalid")
    salt = _decode(payload["salt"])
    expected = _decode(payload["dk"])
    if len(salt) != _SALT_BYTES or len(expected) != _DKLEN:
        raise InvalidInput("password verifier is invalid")
    return salt, expected


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: object) -> bytes:
    if type(value) is not str:
        raise InvalidInput("password verifier is invalid")
    try:
        return base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise InvalidInput("password verifier is invalid") from None
