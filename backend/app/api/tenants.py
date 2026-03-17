"""Tenant (Company) management API.

Public endpoints for self-service company creation and joining.
Admin endpoints for platform-level company management.
"""

import re
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func as sqla_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_role
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User

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
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = None
    im_provider: str | None = None
    timezone: str | None = None
    is_active: bool | None = None


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

    # Assign creator as org_admin
    current_user.tenant_id = tenant.id
    current_user.role = "org_admin" if current_user.role == "member" else current_user.role
    # Inherit quota defaults from new tenant
    current_user.quota_message_limit = tenant.default_message_limit
    current_user.quota_message_period = tenant.default_message_period
    current_user.quota_max_agents = tenant.default_max_agents
    current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
    await db.flush()

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
    return TenantOut.model_validate(tenant)


@router.put("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update tenant settings."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    for field, value in data.model_dump(exclude_unset=True).items():
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
