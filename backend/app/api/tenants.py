"""Tenant (Company) management API.

Public endpoints for self-service company creation and joining.
Admin endpoints for platform-level company management.
"""

import re
import secrets
import uuid
from datetime import datetime

from loguru import logger

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import func as sqla_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_role
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.models.system_settings import SystemSetting

router = APIRouter(prefix="/tenants", tags=["tenants"])


# ─── Schemas ────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    im_provider: str
    timezone: str = "UTC"
    is_active: bool
    is_default: bool = False
    sso_enabled: bool = False
    sso_domain: str | None = None
    subdomain_prefix: str | None = None
    effective_base_url: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = None
    im_provider: str | None = None
    timezone: str | None = None
    is_active: bool | None = None
    is_default: bool | None = None
    sso_enabled: bool | None = None
    sso_domain: str | None = None
    subdomain_prefix: str | None = None
    effective_base_url: str | None = None


# ─── Helpers ────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Generate a URL-friendly slug from a company name."""
    # Replace CJK and non-alphanumeric chars with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    slug = slug.strip("-")[:40]
    if not slug:
        slug = "company"
    # Add short random suffix for uniqueness
    slug = f"{slug}-{secrets.token_hex(3)}"
    return slug


async def _generate_subdomain_prefix(db: AsyncSession, base: str) -> str:
    """From a slug, generate a unique subdomain_prefix (strips random hex suffix)."""
    # Strip trailing hex suffix added by _slugify (e.g. "-a1b2c3")
    clean = re.sub(r"-[0-9a-f]{6}$", "", base)
    # Keep only lowercase letters, digits, hyphens
    prefix = re.sub(r"[^a-z0-9\-]", "", clean.lower())
    prefix = re.sub(r"-+", "-", prefix).strip("-")
    if len(prefix) < 2:
        prefix = f"co-{prefix}" if prefix else "company"
    if len(prefix) > 50:
        prefix = prefix[:50].rstrip("-")

    # Uniqueness check with counter suffix
    candidate = prefix
    counter = 1
    while True:
        result = await db.execute(
            select(Tenant).where(Tenant.subdomain_prefix == candidate)
        )
        if not result.scalar_one_or_none():
            return candidate
        candidate = f"{prefix}-{counter}"
        counter += 1


# ─── Self-Service: Create Company ───────────────────────

@router.post("/self-create", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def self_create_company(
    data: TenantCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company (self-service). The creator becomes org_admin."""
    # Must not already belong to a company
    if current_user.tenant_id is not None:
        raise HTTPException(status_code=400, detail="You already belong to a company")

    # Check if self-creation is allowed
    from app.models.system_settings import SystemSetting
    setting = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "allow_self_create_company")
    )
    s = setting.scalar_one_or_none()
    allowed = s.value.get("enabled", True) if s else True
    if not allowed and current_user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="Company self-creation is currently disabled")

    slug = _slugify(data.name)
    tenant = Tenant(name=data.name, slug=slug, im_provider="web_only")
    db.add(tenant)
    await db.flush()

    # Auto-generate subdomain_prefix from slug
    if not tenant.subdomain_prefix:
        tenant.subdomain_prefix = await _generate_subdomain_prefix(db, slug)
        await db.flush()

    # Assign creator as org_admin
    current_user.tenant_id = tenant.id
    current_user.role = "org_admin" if current_user.role == "member" else current_user.role
    # Inherit quota defaults from new tenant
    current_user.quota_message_limit = tenant.default_message_limit
    current_user.quota_message_period = tenant.default_message_period
    current_user.quota_max_agents = tenant.default_max_agents
    current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
    await db.flush()

    # Seed default agents (Morty & Meeseeks) for the new company
    try:
        from app.services.agent_seeder import seed_default_agents
        await seed_default_agents(tenant_id=tenant.id, creator_id=current_user.id, db=db)
    except Exception as e:
        logger.warning(f"[self_create_company] Failed to seed default agents: {e}")

    return TenantOut.model_validate(tenant)


# ─── Self-Service: Join Company via Invite Code ─────────

class JoinRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=32)


class JoinResponse(BaseModel):
    tenant: TenantOut
    role: str


@router.post("/join", response_model=JoinResponse)
async def join_company(
    data: JoinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Join an existing company using an invitation code."""
    if current_user.tenant_id is not None:
        raise HTTPException(status_code=400, detail="You already belong to a company")

    from app.models.invitation_code import InvitationCode
    ic_result = await db.execute(
        select(InvitationCode).where(
            InvitationCode.code == data.invitation_code,
            InvitationCode.is_active == True,
            InvitationCode.tenant_id.is_not(None),
        )
    )
    code_obj = ic_result.scalar_one_or_none()
    if not code_obj:
        raise HTTPException(status_code=400, detail="Invalid invitation code")
    if code_obj.used_count >= code_obj.max_uses:
        raise HTTPException(status_code=400, detail="Invitation code has reached its usage limit")

    # Find the company
    t_result = await db.execute(select(Tenant).where(Tenant.id == code_obj.tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=400, detail="Company not found or is disabled")

    # Check if this company has an org_admin already
    admin_check = await db.execute(
        select(sqla_func.count()).select_from(User).where(
            User.tenant_id == tenant.id,
            User.role.in_(["org_admin", "platform_admin"]),
        )
    )
    has_admin = admin_check.scalar() > 0

    # First joiner of an empty company becomes org_admin
    assigned_role = "member" if has_admin else "org_admin"

    # Assign user to company
    current_user.tenant_id = tenant.id
    if current_user.role == "member":
        current_user.role = assigned_role
    # Inherit quota defaults from tenant
    current_user.quota_message_limit = tenant.default_message_limit
    current_user.quota_message_period = tenant.default_message_period
    current_user.quota_max_agents = tenant.default_max_agents
    current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours

    # Increment invitation code usage
    code_obj.used_count += 1
    await db.flush()

    return JoinResponse(
        tenant=TenantOut.model_validate(tenant),
        role=current_user.role,
    )


# ─── Registration Config ───────────────────────────────

@router.get("/registration-config")
async def get_registration_config(db: AsyncSession = Depends(get_db)):
    """Public — returns whether self-creation of companies is allowed."""
    from app.models.system_settings import SystemSetting
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "allow_self_create_company")
    )
    s = result.scalar_one_or_none()
    allowed = s.value.get("enabled", True) if s else True
    return {"allow_self_create_company": allowed}


# ─── Public: Resolve Tenant by Domain ───────────────────

@router.get("/resolve-by-domain")
async def resolve_tenant_by_domain(
    domain: str,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a tenant by its sso_domain or subdomain slug.

    Lookup precedence:
    1. Exact match on tenant.sso_domain (e.g. "acme.clawith.ai")
    2. Extract slug from "{slug}.clawith.ai" and match tenant.slug
    3. Match platform global domain -> return default tenant
    """
    # 1. Try exact sso_domain match first
    result = await db.execute(select(Tenant).where(Tenant.sso_domain == domain))
    tenant = result.scalar_one_or_none()

    # 2. Fallback: extract slug from subdomain pattern
    if not tenant:
        m = re.match(r"^([a-z0-9][a-z0-9\-]*[a-z0-9])\.clawith\.ai$", domain.lower())
        if m:
            slug = m.group(1)
            result = await db.execute(select(Tenant).where(Tenant.slug == slug))
            tenant = result.scalar_one_or_none()

    # 2.5 Subdomain prefix match
    # e.g. domain=acme.clawith.yeyecha.com, global hostname=clawith.yeyecha.com -> prefix acme
    if not tenant:
        from urllib.parse import urlparse as _urlparse
        setting_r2 = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "platform")
        )
        platform2 = setting_r2.scalar_one_or_none()
        if platform2 and platform2.value.get("public_base_url"):
            parsed2 = _urlparse(platform2.value["public_base_url"])
            global_hostname = parsed2.hostname
            if global_hostname and domain.endswith(f".{global_hostname}"):
                prefix = domain[: -(len(global_hostname) + 1)]
                if prefix and "." not in prefix:  # only single-level prefix
                    result = await db.execute(
                        select(Tenant).where(Tenant.subdomain_prefix == prefix)
                    )
                    tenant = result.scalar_one_or_none()

    # 3. Match platform global domain -> return default tenant
    if not tenant:
        from urllib.parse import urlparse
        setting_r = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "platform")
        )
        platform = setting_r.scalar_one_or_none()
        if platform and platform.value.get("public_base_url"):
            parsed = urlparse(platform.value["public_base_url"])
            global_host = parsed.hostname
            if parsed.port and parsed.port not in (80, 443):
                global_host = f"{global_host}:{parsed.port}"
            if domain == global_host or domain == parsed.hostname:
                result = await db.execute(
                    select(Tenant).where(Tenant.is_active == True, Tenant.is_default == True)
                )
                tenant = result.scalar_one_or_none()

    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Tenant not found or not active")

    # Build effective_base_url
    platform_result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "platform")
    )
    platform_setting = platform_result.scalar_one_or_none()
    global_base_url = platform_setting.value.get("public_base_url") if platform_setting else None

    if tenant.sso_domain:
        d = tenant.sso_domain
        effective_base_url = d if d.startswith("http") else f"https://{d}"
    elif tenant.subdomain_prefix and global_base_url:
        from urllib.parse import urlparse as _up
        _p = _up(global_base_url)
        _h = f"{tenant.subdomain_prefix}.{_p.hostname}"
        if _p.port and _p.port not in (80, 443):
            _h = f"{_h}:{_p.port}"
        effective_base_url = f"{_p.scheme}://{_h}"
    elif global_base_url:
        effective_base_url = global_base_url
    else:
        effective_base_url = None

    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "sso_enabled": tenant.sso_enabled,
        "sso_domain": tenant.sso_domain,
        "subdomain_prefix": tenant.subdomain_prefix,
        "is_active": tenant.is_active,
        "effective_base_url": effective_base_url,
    }

# ─── Check Subdomain Prefix Availability ───────────────

@router.get("/check-prefix")
async def check_subdomain_prefix(
    prefix: str,
    exclude_tenant_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Check if a subdomain prefix is available (public)."""
    # Format validation
    if len(prefix) < 2 or not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", prefix):
        return {"available": False, "reason": "Invalid format. Use lowercase letters, numbers, hyphens. Min 2 chars."}

    # Reserved prefixes
    reserved = {"www", "api", "admin", "app", "mail", "ftp", "dev", "staging", "test", "static", "cdn", "ns1", "ns2"}
    if prefix in reserved:
        return {"available": False, "reason": "This prefix is reserved."}

    # Uniqueness check
    query = select(Tenant).where(Tenant.subdomain_prefix == prefix)
    if exclude_tenant_id:
        query = query.where(Tenant.id != exclude_tenant_id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        return {"available": False, "reason": "This prefix is already taken."}

    return {"available": True}



# ─── Authenticated: List / Get ──────────────────────────

@router.get("/", response_model=list[TenantOut])
async def list_tenants(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants (platform_admin only)."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return [TenantOut.model_validate(t) for t in result.scalars().all()]


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tenant details. Platform admins can view any; org_admins only their own."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if current_user.role == "org_admin" and str(current_user.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Access denied")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Build effective_base_url
    platform_result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "platform")
    )
    platform_setting = platform_result.scalar_one_or_none()
    global_base_url = platform_setting.value.get("public_base_url") if platform_setting else None

    if tenant.sso_domain:
        d = tenant.sso_domain
        effective_base_url = d if d.startswith("http") else f"https://{d}"
    elif tenant.subdomain_prefix and global_base_url:
        from urllib.parse import urlparse as _up2
        _p2 = _up2(global_base_url)
        _h2 = f"{tenant.subdomain_prefix}.{_p2.hostname}"
        if _p2.port and _p2.port not in (80, 443):
            _h2 = f"{_h2}:{_p2.port}"
        effective_base_url = f"{_p2.scheme}://{_h2}"
    elif global_base_url:
        effective_base_url = global_base_url
    else:
        effective_base_url = None

    out = TenantOut.model_validate(tenant).model_dump()
    out["effective_base_url"] = effective_base_url
    return out


@router.put("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update tenant settings. Platform admins can update any; org_admins only their own."""
    if current_user.role == "org_admin" and str(current_user.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Can only update your own company")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = data.model_dump(exclude_unset=True)
    
    # SSO configuration is managed exclusively by the company's own org_admin
    # via the Enterprise Settings page. Platform admins should not override it here.
    if current_user.role == "platform_admin":
        update_data.pop("sso_enabled", None)
        update_data.pop("sso_domain", None)

    # effective_base_url is computed, not stored
    update_data.pop("effective_base_url", None)

    # Slug is not updatable via this API; ignore any slug field
    update_data.pop("slug", None)

    # Handle is_default: only platform_admin can set it
    if "is_default" in update_data:
        if current_user.role != "platform_admin":
            update_data.pop("is_default", None)
        elif not update_data["is_default"] and tenant.is_default:
            # Prevent disabling the current default company directly
            raise HTTPException(
                status_code=400,
                detail="Cannot disable default company directly. Set another company as default instead."
            )
        elif update_data["is_default"]:
            # Clear is_default on all other tenants first
            await db.execute(
                sa.update(Tenant).where(Tenant.id != tenant_id).values(is_default=False)
            )

    # Validate subdomain_prefix if provided
    if "subdomain_prefix" in update_data:
        prefix = update_data["subdomain_prefix"]
        if prefix:
            if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", prefix) or len(prefix) < 2:
                raise HTTPException(status_code=400, detail="Invalid subdomain prefix format. Use lowercase letters, numbers, hyphens. Min 2 chars.")
            reserved = {"www", "api", "admin", "app", "mail", "ftp", "dev", "staging", "test", "static", "cdn"}
            if prefix in reserved:
                raise HTTPException(status_code=400, detail="This subdomain prefix is reserved")
        update_data["subdomain_prefix"] = prefix or None

    for field, value in update_data.items():
        setattr(tenant, field, value)
    await db.flush()
    return TenantOut.model_validate(tenant)


@router.put("/{tenant_id}/assign-user/{user_id}")
async def assign_user_to_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "member",
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Assign a user to a tenant with a specific role."""
    # Verify tenant
    t_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if not t_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Verify user
    u_result = await db.execute(select(User).where(User.id == user_id))
    user = u_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if role not in ("org_admin", "agent_admin", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")

    user.tenant_id = tenant_id
    user.role = role
    await db.flush()
    return {"status": "ok", "user_id": str(user_id), "tenant_id": str(tenant_id), "role": role}
