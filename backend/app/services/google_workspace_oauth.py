"""Shared helpers for Google Workspace OAuth flows."""

import hashlib
import hmac
import uuid

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.services.platform_service import platform_service

settings = get_settings()

GOOGLE_SSO_STATE_KIND = "google_sso"
GOOGLE_SYNC_STATE_KIND = "google_sync"
GOOGLE_CALLBACK_PATH = "/auth/google_workspace/callback"
GOOGLE_HTTP_PROXY = settings.HTTP_PROXY or None


def sign_google_oauth_state(kind: str, value: uuid.UUID) -> str:
    raw = str(value)
    payload = f"{kind}:{raw}"
    sig = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def parse_google_oauth_state(state: str) -> tuple[str, uuid.UUID] | None:
    parts = state.split(":")
    if len(parts) != 3:
        return None

    kind, raw, sig = parts
    if kind not in {GOOGLE_SSO_STATE_KIND, GOOGLE_SYNC_STATE_KIND}:
        return None

    payload = f"{kind}:{raw}"
    expected = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        return kind, uuid.UUID(raw)
    except ValueError:
        return None


async def get_google_provider(db: AsyncSession, provider_id: uuid.UUID) -> IdentityProvider:
    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider or provider.provider_type != "google_workspace":
        raise HTTPException(status_code=404, detail="Google Workspace provider not found")
    return provider


async def get_google_provider_base_url(
    db: AsyncSession,
    provider: IdentityProvider,
    request: Request | None = None,
) -> str:
    tenant = None
    if provider.tenant_id:
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == provider.tenant_id))
        tenant = tenant_result.scalar_one_or_none()
    if tenant:
        return await platform_service.get_tenant_sso_base_url(db, tenant, request)
    return await platform_service.get_public_base_url(db, request)


async def get_google_redirect_uri(
    db: AsyncSession,
    provider: IdentityProvider,
    request: Request | None = None,
) -> str:
    base_url = await get_google_provider_base_url(db, provider, request)
    return f"{base_url}/api{GOOGLE_CALLBACK_PATH}"


async def probe_google_directory(access_token: str, customer_id: str = "my_customer") -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=20, proxy=GOOGLE_HTTP_PROXY) as client:
        org_resp = await client.get(
            f"https://admin.googleapis.com/admin/directory/v1/customer/{customer_id}/orgunits",
            params={"type": "all"},
            headers=headers,
        )
        if org_resp.status_code >= 400:
            raise RuntimeError(f"Google orgunits probe failed: {org_resp.json()}")

        user_resp = await client.get(
            "https://admin.googleapis.com/admin/directory/v1/users",
            params={"customer": customer_id, "maxResults": 1, "orderBy": "email"},
            headers=headers,
        )
        if user_resp.status_code >= 400:
            raise RuntimeError(f"Google users probe failed: {user_resp.json()}")
