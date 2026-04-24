"""Codex OAuth provisioning API.

Flow (browser):
  1) POST /llm-models/codex-oauth/start  — backend mints PKCE verifier/state,
     tries to bind a local loopback listener on 127.0.0.1:1455, returns the
     authorize URL (+ whether the loopback is available).
  2) User's browser navigates to the authorize URL, logs in with ChatGPT, and
     is redirected back to http://localhost:1455/auth/callback?code=...&state=...
  3) Frontend polls GET /llm-models/codex-oauth/poll?state=X until a code
     appears (loopback mode), OR the user manually pastes the redirect URL.
  4) POST /llm-models/codex-oauth/complete — backend exchanges code for tokens
     and creates an LLMModel row with auth_type='codex_oauth'.

Alternative (Mode B / no loopback):
  POST /llm-models/codex-oauth/paste-creds — user pastes access/refresh/expiry
  (e.g. from a local `codex login` run) and Clawith starts managing refresh.

The loopback listener uses stdlib http.server in a background thread. Only one
binding per process; if the port is already held (by another Clawith worker or
an unrelated process), `/start` returns loopback_ready=false and the frontend
must fall back to Mode B or to manual URL paste.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import encrypt_data, get_current_admin
from app.database import get_db
from app.models.llm import LLMModel
from app.services.llm.codex_oauth import (
    CODEX_OAUTH_MODELS,
    LOOPBACK_HOST,
    LOOPBACK_PORT,
    REDIRECT_URI,
    build_authorize_url,
    decode_account_id,
    exchange_code,
    generate_pkce,
    generate_state,
)

router = APIRouter(prefix="/llm-models/codex-oauth", tags=["codex-oauth"])

# ─── In-memory OAuth session cache (per backend process) ──────────────────────
_SESSION_TTL = timedelta(minutes=10)
_sessions_lock = threading.Lock()
_sessions: dict[str, dict[str, Any]] = {}
# Keyed by state. Each entry: {"verifier": str, "expires_at": datetime, "code": str | None, "error": str | None}

_listener_lock = threading.Lock()
_listener: HTTPServer | None = None
_listener_thread: threading.Thread | None = None


def _put_session(state: str, verifier: str) -> None:
    _gc_sessions()
    with _sessions_lock:
        _sessions[state] = {
            "verifier": verifier,
            "expires_at": datetime.now(tz=timezone.utc) + _SESSION_TTL,
            "code": None,
            "error": None,
        }


def _get_session(state: str) -> dict[str, Any] | None:
    with _sessions_lock:
        entry = _sessions.get(state)
        if entry is None:
            return None
        if entry["expires_at"] < datetime.now(tz=timezone.utc):
            _sessions.pop(state, None)
            return None
        return dict(entry)


def _record_callback(state: str, code: str | None, error: str | None) -> None:
    with _sessions_lock:
        entry = _sessions.get(state)
        if entry is None:
            return
        if code:
            entry["code"] = code
        if error:
            entry["error"] = error


def _consume_session(state: str) -> dict[str, Any] | None:
    """Read-and-remove a session on successful code exchange."""
    with _sessions_lock:
        return _sessions.pop(state, None)


def _gc_sessions() -> None:
    now = datetime.now(tz=timezone.utc)
    with _sessions_lock:
        dead = [s for s, v in _sessions.items() if v["expires_at"] < now]
        for s in dead:
            _sessions.pop(s, None)


# ─── Loopback listener ────────────────────────────────────────────────────────
class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — required method name
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        code = (params.get("code") or [None])[0]
        error = (params.get("error") or [None])[0]

        if not state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing state")
            return

        _record_callback(state, code, error)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><html><body style='font-family:system-ui;padding:40px'>"
            b"<h2>Authentication received</h2>"
            b"<p>You can close this tab and return to Clawith.</p>"
            b"</body></html>"
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Suppress default stderr spam from BaseHTTPRequestHandler
        return


def _ensure_listener() -> bool:
    """Start the loopback listener if not already running in this process.

    Returns True if it's running (freshly or pre-existing) and False if the
    port couldn't be bound (typically because another process holds it).
    """
    global _listener, _listener_thread
    with _listener_lock:
        if _listener is not None:
            return True
        try:
            _listener = HTTPServer((LOOPBACK_HOST, LOOPBACK_PORT), _CallbackHandler)
        except OSError as e:
            logger.warning(f"[codex_oauth] Could not bind {LOOPBACK_HOST}:{LOOPBACK_PORT}: {e}")
            _listener = None
            return False
        _listener_thread = threading.Thread(
            target=_listener.serve_forever,
            name="codex-oauth-loopback",
            daemon=True,
        )
        _listener_thread.start()
        logger.info(f"[codex_oauth] Loopback listener bound to {LOOPBACK_HOST}:{LOOPBACK_PORT}")
        return True


# ─── Schemas ──────────────────────────────────────────────────────────────────
class StartRequest(BaseModel):
    pass


class StartResponse(BaseModel):
    authorize_url: str
    state: str
    redirect_uri: str
    loopback_ready: bool
    manual_paste_hint: str = Field(
        default=(
            "If the loopback isn't available (port 1455 busy, or Clawith is on a "
            "remote host), complete the login in your browser, then paste the full "
            "redirect URL (or just the code) into /complete."
        )
    )


class PollResponse(BaseModel):
    code: str | None = None
    error: str | None = None
    expired: bool = False


class CompleteRequest(BaseModel):
    state: str
    code: str
    label: str
    model: str
    enabled: bool = True


class PasteCredsRequest(BaseModel):
    access_token: str
    refresh_token: str
    expires_in_seconds: int = 3600
    account_id: str | None = None
    label: str
    model: str
    enabled: bool = True


class ModelCreatedResponse(BaseModel):
    id: uuid.UUID
    label: str
    provider: str
    model: str
    oauth_account_id: str | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _reject_non_codex_model(model: str) -> None:
    if model not in CODEX_OAUTH_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Model '{model}' is not a Codex OAuth-supported model. "
                f"Allowed: {', '.join(CODEX_OAUTH_MODELS)}"
            ),
        )


def _resolve_tenant_id(tenant_id_override: str | None, current_user: Any) -> UUID | None:
    """Resolve the target tenant for a new LLM model row.

    Mirrors the override semantics of `add_llm_model` in enterprise.py so a
    platform admin managing another tenant can create OAuth models on that
    tenant's behalf. Falls back to the caller's own tenant.
    """
    raw = tenant_id_override or (
        str(current_user.tenant_id) if getattr(current_user, "tenant_id", None) else None
    )
    if not raw:
        return None
    try:
        return UUID(raw)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tenant_id: {raw!r}",
        ) from e


async def _insert_oauth_model(
    db: AsyncSession,
    *,
    current_user: Any,
    tenant_id_override: str | None,
    label: str,
    model: str,
    access_token: str,
    refresh_token_value: str,
    expires_at: datetime,
    account_id: str | None,
) -> LLMModel:
    settings = get_settings()
    row = LLMModel(
        tenant_id=_resolve_tenant_id(tenant_id_override, current_user),
        provider="codex-oauth",
        model=model,
        label=label,
        auth_type="codex_oauth",
        api_key_encrypted=None,
        oauth_access_token_encrypted=encrypt_data(access_token, settings.SECRET_KEY),
        oauth_refresh_token_encrypted=encrypt_data(refresh_token_value, settings.SECRET_KEY),
        oauth_expires_at=expires_at,
        oauth_account_id=account_id,
        base_url="https://chatgpt.com/backend-api",
        enabled=True,
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)
    return row


# ─── Endpoints ────────────────────────────────────────────────────────────────
@router.post("/start", response_model=StartResponse)
async def start_oauth(
    _req: StartRequest | None = None,
    current_user: Any = Depends(get_current_admin),
) -> StartResponse:
    """Kick off a Codex OAuth flow: mint PKCE+state, try to bind loopback, return authorize URL."""
    pkce = generate_pkce()
    state = generate_state()
    _put_session(state, pkce.verifier)
    loopback_ready = _ensure_listener()
    authorize_url = build_authorize_url(pkce.challenge, state)
    return StartResponse(
        authorize_url=authorize_url,
        state=state,
        redirect_uri=REDIRECT_URI,
        loopback_ready=loopback_ready,
    )


@router.get("/poll", response_model=PollResponse)
async def poll_oauth(
    state: str,
    current_user: Any = Depends(get_current_admin),
) -> PollResponse:
    """Check whether the loopback listener has received the auth code yet."""
    entry = _get_session(state)
    if entry is None:
        return PollResponse(expired=True)
    return PollResponse(code=entry.get("code"), error=entry.get("error"))


@router.post("/complete", response_model=ModelCreatedResponse)
async def complete_oauth(
    req: CompleteRequest,
    tenant_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_admin),
) -> ModelCreatedResponse:
    """Exchange the authorization code for tokens and persist the model.

    `tenant_id` (query) is optional and matches the semantics of
    `POST /enterprise/llm-models`: a platform admin can provision the model
    on behalf of another tenant. Without it, the caller's own tenant is used.
    """
    _reject_non_codex_model(req.model)

    entry = _consume_session(req.state)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth session not found or expired. Restart the flow via /start.",
        )

    try:
        bundle = await exchange_code(req.code, entry["verifier"])
    except Exception as e:
        logger.exception("[codex_oauth] exchange_code failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token exchange failed: {e}",
        ) from e

    row = await _insert_oauth_model(
        db,
        current_user=current_user,
        tenant_id_override=tenant_id,
        label=req.label,
        model=req.model,
        access_token=bundle.access_token,
        refresh_token_value=bundle.refresh_token,
        expires_at=bundle.expires_at,
        account_id=bundle.account_id,
    )
    return ModelCreatedResponse(
        id=row.id,
        label=row.label,
        provider=row.provider,
        model=row.model,
        oauth_account_id=row.oauth_account_id,
    )


@router.post("/paste-creds", response_model=ModelCreatedResponse)
async def paste_credentials(
    req: PasteCredsRequest,
    tenant_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_admin),
) -> ModelCreatedResponse:
    """Mode B fallback: import tokens directly (e.g. from ~/.codex/auth.json).

    `tenant_id` (query) — same override semantics as `/complete`.
    """
    _reject_non_codex_model(req.model)
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=max(0, req.expires_in_seconds))
    account_id = req.account_id or decode_account_id(req.access_token)
    row = await _insert_oauth_model(
        db,
        current_user=current_user,
        tenant_id_override=tenant_id,
        label=req.label,
        model=req.model,
        access_token=req.access_token,
        refresh_token_value=req.refresh_token,
        expires_at=expires_at,
        account_id=account_id,
    )
    return ModelCreatedResponse(
        id=row.id,
        label=row.label,
        provider=row.provider,
        model=row.model,
        oauth_account_id=row.oauth_account_id,
    )
