"""OAuth 2.1 + PKCE client for OpenAI Codex (ChatGPT Plus/Pro subscription).

Lets Clawith act as a third-party OAuth client against OpenAI's authorization
server so users can authenticate with their ChatGPT subscription instead of an
API key. The resulting access token is used to call the Codex inference endpoint
at https://chatgpt.com/backend-api/responses.

All constants below are intentionally hardcoded — they mirror the values used by
the official Codex CLI and by community integrations (OpenClaw, Hermes,
numman-ali/opencode-openai-codex-auth). The client_id is a public, shared OSS
identifier, not a secret.

This module contains only pure primitives (no DB, no HTTP server, no FastAPI
dependency) so it can be unit-tested in isolation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

# ─── OAuth constants ──────────────────────────────────────────────────────────
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
# REDIRECT_URI must match what OpenAI registered for this public client_id. Do
# not change — the value is fixed on OpenAI's side. The loopback listener just
# needs to service this URL from the user's browser perspective.
REDIRECT_URI = "http://localhost:1455/auth/callback"
# Default loopback listener bind. In a Docker deployment, set
# CODEX_OAUTH_LOOPBACK_HOST=0.0.0.0 so the mapped host port can reach the
# container; the listener surface only handles /auth/callback with state, so
# exposing it inside a private network is safe.
LOOPBACK_HOST = os.environ.get("CODEX_OAUTH_LOOPBACK_HOST", "127.0.0.1")
LOOPBACK_PORT = int(os.environ.get("CODEX_OAUTH_LOOPBACK_PORT", "1455"))
SCOPE = "openid profile email offline_access"

# ─── Inference constants ──────────────────────────────────────────────────────
CODEX_BASE_URL = "https://chatgpt.com/backend-api"
ORIGINATOR = "codex_cli_rs"
OPENAI_BETA = "responses=experimental"
# JWT claim that holds the ChatGPT account record; used for chatgpt-account-id header
JWT_AUTH_CLAIM = "https://api.openai.com/auth"

# ─── Models the Codex OAuth endpoint accepts ──────────────────────────────────
CODEX_OAUTH_MODELS = (
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex-max",
    "gpt-5.2",
    "gpt-5.2-codex",
    "codex-mini-latest",
)

# Refresh a bit before actual expiry to avoid racing against wall clock skew
_REFRESH_LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class PKCEPair:
    verifier: str
    challenge: str


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: datetime  # timezone-aware UTC
    account_id: str | None = None


def generate_pkce() -> PKCEPair:
    """Generate a PKCE verifier/challenge pair per RFC 7636 (S256)."""
    # 64 URL-safe bytes → ~86 chars, within RFC 7636 limit (43–128)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    """CSRF state token for the OAuth flow."""
    return secrets.token_hex(16)


def build_authorize_url(challenge: str, state: str, redirect_uri: str = REDIRECT_URI) -> str:
    """Build the OpenAI authorize URL for the PKCE flow.

    Includes `codex_cli_simplified_flow` / `id_token_add_organizations` /
    `originator` params that the Codex CLI sends; without these the authorize
    page behaves differently.
    """
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _parse_token_response(payload: dict) -> TokenBundle:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh):
        raise ValueError(f"token response missing access_token/refresh_token: {payload!r}")
    if not isinstance(expires_in, int):
        raise ValueError(f"token response missing or non-integer expires_in: {payload!r}")
    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in),
        account_id=decode_account_id(access),
    )


async def exchange_code(
    code: str,
    verifier: str,
    redirect_uri: str = REDIRECT_URI,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Exchange an authorization code (+ PKCE verifier) for access+refresh tokens."""
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    return await _post_token(data, client)


async def refresh_token(
    refresh_token_value: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Trade a refresh_token for a new access+refresh pair."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": CLIENT_ID,
    }
    return await _post_token(data, client)


async def _post_token(data: dict, client: httpx.AsyncClient | None) -> TokenBundle:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await http.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise ValueError(
                f"token endpoint returned {resp.status_code}: {resp.text[:500]}"
            )
        return _parse_token_response(resp.json())
    finally:
        if owns_client:
            await http.aclose()


def decode_jwt_payload(token: str) -> dict | None:
    """Base64-decode the payload segment of a JWT (no signature verification)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    # base64url — pad to multiple of 4
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def decode_account_id(access_token_jwt: str) -> str | None:
    """Extract the ChatGPT account id from the access token JWT's auth claim."""
    payload = decode_jwt_payload(access_token_jwt)
    if not isinstance(payload, dict):
        return None
    claim = payload.get(JWT_AUTH_CLAIM)
    if not isinstance(claim, dict):
        return None
    account_id = claim.get("chatgpt_account_id") or claim.get("account_id")
    return account_id if isinstance(account_id, str) else None


def is_near_expiry(expires_at: datetime, leeway_seconds: int = _REFRESH_LEEWAY_SECONDS) -> bool:
    """True if the token expires within the leeway window (or is already expired)."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc) + timedelta(seconds=leeway_seconds) >= expires_at
