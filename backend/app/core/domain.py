"""Domain resolution with fallback chain."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models.system_settings import SystemSetting


async def resolve_base_url(
    db: AsyncSession,
    request: Request | None = None,
    tenant_id: str | None = None,
) -> str:
    """Resolve the effective base URL using the fallback chain:
    
    1. Tenant-specific sso_domain (if tenant_id provided and tenant has sso_domain)
    2. Platform global public_base_url (from system_settings)
    3. Request origin (from request.base_url)
    4. Hardcoded fallback
    
    Returns a full URL like "https://acme.example.com" or "http://localhost:3008"
    """
    # Level 1: Tenant-specific domain
    if tenant_id:
        from app.models.tenant import Tenant
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant and tenant.sso_domain:
            domain = tenant.sso_domain
            # sso_domain stores pure domain (with optional port), add https
            return f"https://{domain}".rstrip("/")
    
    # Level 2: Platform global setting
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "platform")
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value.get("public_base_url"):
        return setting.value["public_base_url"].rstrip("/")
    
    # Level 3: Request origin
    if request:
        return str(request.base_url).rstrip("/")
    
    # Level 4: Hardcoded fallback
    return "http://localhost:8000"
