"""Domain resolution with fallback chain."""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models.system_settings import SystemSetting


async def _get_global_base_url(db: AsyncSession):
    """Helper: read platform public_base_url from system_settings."""
    # Try DB first
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "platform")
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value.get("public_base_url"):
        return setting.value["public_base_url"].rstrip("/")
    # Fallback to ENV
    env_url = os.environ.get("PUBLIC_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    return None


async def resolve_base_url(
    db: AsyncSession,
    request: Request | None = None,
    tenant_id: str | None = None,
) -> str:
    """Resolve the effective base URL using the fallback chain:

    1. Tenant-specific sso_domain (if tenant_id provided and tenant has sso_domain)
    2. Tenant subdomain_prefix + global hostname
    3. Platform global public_base_url (from system_settings)
    4. Request origin (from request.base_url)
    5. Hardcoded fallback

    Returns a full URL like "https://acme.example.com" or "http://localhost:3008"
    """
    # Level 1 & 2: Tenant-specific
    if tenant_id:
        from app.models.tenant import Tenant
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant:
            # Level 1: complete custom domain
            if tenant.sso_domain:
                domain = tenant.sso_domain.rstrip("/")
                if domain.startswith("http://") or domain.startswith("https://"):
                    return domain
                return f"https://{domain}"

            # Level 2: subdomain prefix + global hostname (skip for default tenant)
            if tenant.subdomain_prefix and not getattr(tenant, 'is_default', False):
                global_url = await _get_global_base_url(db)
                if global_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(global_url)
                    host = f"{tenant.subdomain_prefix}.{parsed.hostname}"
                    if parsed.port and parsed.port not in (80, 443):
                        host = f"{host}:{parsed.port}"
                    return f"{parsed.scheme}://{host}"

    # Level 3: Platform global setting
    global_url = await _get_global_base_url(db)
    if global_url:
        return global_url

    # Level 4: Request origin
    if request:
        return str(request.base_url).rstrip("/")

    # Level 5: Hardcoded fallback
    return "http://localhost:8000"
