"""Unit tests for app.services.llm.codex_oauth.

Covers the pure primitives: PKCE generation, authorize URL shape, token
exchange/refresh over a mocked httpx client, and JWT payload decoding. No DB or
FastAPI fixtures — that lives in the integration test layer.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.llm import codex_oauth as co


# ── PKCE ────────────────────────────────────────────────────────────────

def test_generate_pkce_pair_is_consistent():
    pair = co.generate_pkce()
    # RFC 7636: challenge = base64url(sha256(verifier)), no padding
    digest = hashlib.sha256(pair.verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert pair.challenge == expected
    # Verifier length must be in 43..128 per RFC
    assert 43 <= len(pair.verifier) <= 128
    # Characters must be URL-safe
    assert all(c.isalnum() or c in "-_" for c in pair.verifier)


def test_generate_pkce_produces_unique_pairs():
    seen = {co.generate_pkce().verifier for _ in range(20)}
    assert len(seen) == 20


def test_generate_state_is_hex():
    s = co.generate_state()
    assert len(s) == 32
    int(s, 16)  # must parse as hex


# ── Authorize URL ───────────────────────────────────────────────────────

def test_build_authorize_url_has_required_params():
    url = co.build_authorize_url(challenge="chal", state="st")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == [co.CLIENT_ID]
    assert q["redirect_uri"] == [co.REDIRECT_URI]
    assert q["scope"] == [co.SCOPE]
    assert q["code_challenge"] == ["chal"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]
    # Codex-specific opt-ins the CLI sends — behavior of the login page changes
    # without them, so lock them in.
    assert q["codex_cli_simplified_flow"] == ["true"]
    assert q["id_token_add_organizations"] == ["true"]
    assert q["originator"] == [co.ORIGINATOR]


# ── Token parsing ───────────────────────────────────────────────────────

def _fake_jwt(payload: dict) -> str:
    """Build a JWT-shaped string (header.payload.signature, no real sig)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body_raw = json.dumps(payload).encode("utf-8")
    body = base64.urlsafe_b64encode(body_raw).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.sig"


def test_decode_jwt_payload_roundtrip():
    payload = {"sub": "user-123", co.JWT_AUTH_CLAIM: {"chatgpt_account_id": "acc-xyz"}}
    jwt = _fake_jwt(payload)
    decoded = co.decode_jwt_payload(jwt)
    assert decoded == payload


def test_decode_account_id_prefers_chatgpt_account_id():
    jwt = _fake_jwt({co.JWT_AUTH_CLAIM: {"chatgpt_account_id": "acc-xyz"}})
    assert co.decode_account_id(jwt) == "acc-xyz"


def test_decode_account_id_falls_back_to_account_id():
    jwt = _fake_jwt({co.JWT_AUTH_CLAIM: {"account_id": "acc-fallback"}})
    assert co.decode_account_id(jwt) == "acc-fallback"


def test_decode_account_id_returns_none_for_invalid_jwt():
    assert co.decode_account_id("not-a-jwt") is None
    assert co.decode_account_id("one.two") is None


# ── Exchange / refresh ──────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, body: dict | str):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    def json(self) -> dict:
        if isinstance(self._body, dict):
            return self._body
        return json.loads(self._body)


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_call: dict | None = None

    async def post(self, url, data=None, headers=None):
        self.last_call = {"url": url, "data": dict(data or {}), "headers": dict(headers or {})}
        return self._response

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_exchange_code_builds_correct_request_and_parses_tokens():
    jwt = _fake_jwt({co.JWT_AUTH_CLAIM: {"chatgpt_account_id": "acc-42"}})
    resp = _FakeResponse(
        200,
        {
            "access_token": jwt,
            "refresh_token": "rt-123",
            "expires_in": 3600,
        },
    )
    client = _FakeHttpClient(resp)

    before = datetime.now(tz=timezone.utc)
    bundle = await co.exchange_code(code="ac-code", verifier="ver", client=client)
    after = datetime.now(tz=timezone.utc)

    assert bundle.access_token == jwt
    assert bundle.refresh_token == "rt-123"
    assert bundle.account_id == "acc-42"
    # Expiry should be approximately now + 3600s
    assert before.timestamp() + 3500 <= bundle.expires_at.timestamp() <= after.timestamp() + 3700

    # Request shape
    assert client.last_call["url"] == co.TOKEN_URL
    assert client.last_call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    data = client.last_call["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["client_id"] == co.CLIENT_ID
    assert data["code"] == "ac-code"
    assert data["code_verifier"] == "ver"
    assert data["redirect_uri"] == co.REDIRECT_URI


@pytest.mark.asyncio
async def test_refresh_token_sends_refresh_grant():
    resp = _FakeResponse(
        200,
        {
            "access_token": _fake_jwt({}),
            "refresh_token": "rt-new",
            "expires_in": 1800,
        },
    )
    client = _FakeHttpClient(resp)
    bundle = await co.refresh_token("rt-old", client=client)
    data = client.last_call["data"]
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "rt-old"
    assert data["client_id"] == co.CLIENT_ID
    assert bundle.refresh_token == "rt-new"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_http_error():
    resp = _FakeResponse(400, {"error": "invalid_grant"})
    client = _FakeHttpClient(resp)
    with pytest.raises(ValueError, match="token endpoint returned 400"):
        await co.exchange_code(code="x", verifier="y", client=client)


@pytest.mark.asyncio
async def test_exchange_code_raises_on_missing_fields():
    resp = _FakeResponse(200, {"access_token": "", "refresh_token": "", "expires_in": 0})
    client = _FakeHttpClient(resp)
    with pytest.raises(ValueError):
        await co.exchange_code(code="x", verifier="y", client=client)


# ── Expiry helper ───────────────────────────────────────────────────────

def test_is_near_expiry_triggers_within_leeway():
    from datetime import timedelta

    soon = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
    assert co.is_near_expiry(soon) is True

    later = datetime.now(tz=timezone.utc) + timedelta(seconds=3600)
    assert co.is_near_expiry(later) is False


def test_is_near_expiry_accepts_naive_datetime():
    from datetime import timedelta

    naive_future = (datetime.now(tz=timezone.utc) + timedelta(seconds=3600)).replace(tzinfo=None)
    # Should treat naive as UTC and not blow up
    assert co.is_near_expiry(naive_future) is False
