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

from app.core.security import get_current_user, require_role, get_authenticated_user
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(prefix="/tenants", tags=["tenants"])


# ─── Schemas ────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_tenant_id: uuid.UUID | None = None

class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    im_provider: str
    timezone: str = "UTC"
    is_active: bool
    sso_enabled: bool = False
    sso_domain: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = None
    im_provider: str | None = None
    timezone: str | None = None
    is_active: bool | None = None
    sso_enabled: bool | None = None
    sso_domain: str | None = None


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


class SelfCreateResponse(BaseModel):
    """Response for self-create company, includes token for context switching."""
    tenant: TenantOut
    access_token: str | None = None  # Non-null when a new User record was created (multi-tenant switch)


@router.post("/self-create", response_model=SelfCreateResponse, status_code=status.HTTP_201_CREATED)
async def self_create_company(
    data: TenantCreate,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company (self-service). The creator becomes org_admin.

    Supports both:
    - Registration flow (user has no tenant yet): assigns tenant directly
    - Switch-org flow (user already has a tenant): creates a new User record for the new tenant
    """
    # Block self-creation if locked to a specific tenant (Dedicated Link flow)
    if data.target_tenant_id is not None:
        raise HTTPException(status_code=403, detail="Company creation is not allowed via this link. Please join your assigned organization.")

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

    access_token = None

    if current_user.tenant_id is not None:
        # Multi-tenant: user already belongs to a company.
        # Create a NEW User record for the new tenant instead of overwriting.
        from app.core.security import create_access_token
        from app.models.participant import Participant

        new_user = User(
            identity_id=current_user.identity_id,
            tenant_id=tenant.id,
            display_name=current_user.display_name,
            role="org_admin",
            registration_source="web",
            is_active=current_user.is_active,
            quota_message_limit=tenant.default_message_limit,
            quota_message_period=tenant.default_message_period,
            quota_max_agents=tenant.default_max_agents,
            quota_agent_ttl_hours=tenant.default_agent_ttl_hours,
        )
        db.add(new_user)
        await db.flush()

        # Create Participant for the new user record
        db.add(Participant(
            type="user",
            ref_id=new_user.id,
            display_name=new_user.display_name,
            avatar_url=new_user.avatar_url,
        ))
        await db.flush()

        # Generate token scoped to the new user so frontend can switch context
        access_token = create_access_token(str(new_user.id), new_user.role)
    else:
        # Registration flow: user has no tenant yet, assign directly
        current_user.tenant_id = tenant.id
        current_user.role = "org_admin" if current_user.role == "member" else current_user.role
        # Inherit quota defaults from new tenant
        current_user.quota_message_limit = tenant.default_message_limit
        current_user.quota_message_period = tenant.default_message_period
        current_user.quota_max_agents = tenant.default_max_agents
        current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
        await db.flush()

    await db.commit()

    return SelfCreateResponse(
        tenant=TenantOut.model_validate(tenant),
        access_token=access_token,
    )


# ─── Self-Service: Join Company via Invite Code ─────────

class JoinRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=32)
    target_tenant_id: uuid.UUID | None = None


class JoinResponse(BaseModel):
    tenant: TenantOut
    role: str
    access_token: str | None = None  # Non-null when a new User record was created (multi-tenant switch)


@router.post("/join", response_model=JoinResponse)
async def join_company(
    data: JoinRequest,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Join an existing company using an invitation code.

    Supports both:
    - Registration flow (user has no tenant yet): assigns tenant directly
    - Switch-org flow (user already has a tenant): creates a new User record"""
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

    # Verify matching tenant if locked (Dedicated Link flow)
    if data.target_tenant_id and str(code_obj.tenant_id) != str(data.target_tenant_id):
        raise HTTPException(status_code=403, detail="This invitation code does not belong to the required organization.")

    if code_obj.used_count >= code_obj.max_uses:
        raise HTTPException(status_code=400, detail="Invitation code has reached its usage limit")

    # Find the company
    t_result = await db.execute(select(Tenant).where(Tenant.id == code_obj.tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=400, detail="Company not found or is disabled")

    # Check if user already belongs to this specific tenant
    existing_membership = await db.execute(
        select(User).where(
            User.identity_id == current_user.identity_id,
            User.tenant_id == tenant.id,
        )
    )
    if existing_membership.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You already belong to this company")

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

    access_token = None

    if current_user.tenant_id is not None:
        # Multi-tenant: user already belongs to a company.
        # Create a NEW User record for the new tenant.
        from app.core.security import create_access_token
        from app.models.participant import Participant

        new_user = User(
            identity_id=current_user.identity_id,
            tenant_id=tenant.id,
            display_name=current_user.display_name,
            role=assigned_role,
            registration_source="web",
            is_active=current_user.is_active,
            quota_message_limit=tenant.default_message_limit,
            quota_message_period=tenant.default_message_period,
            quota_max_agents=tenant.default_max_agents,
            quota_agent_ttl_hours=tenant.default_agent_ttl_hours,
        )
        db.add(new_user)
        await db.flush()

        # Create Participant for the new user record
        db.add(Participant(
            type="user",
            ref_id=new_user.id,
            display_name=new_user.display_name,
            avatar_url=new_user.avatar_url,
        ))
        await db.flush()

        # Generate token scoped to the new user so frontend can switch context
        access_token = create_access_token(str(new_user.id), new_user.role)
        final_role = new_user.role
    else:
        # Registration flow: user has no tenant yet, assign directly
        current_user.tenant_id = tenant.id
        if current_user.role == "member":
            current_user.role = assigned_role
        # Inherit quota defaults from tenant
        current_user.quota_message_limit = tenant.default_message_limit
        current_user.quota_message_period = tenant.default_message_period
        current_user.quota_max_agents = tenant.default_max_agents
        current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
        final_role = current_user.role

    # Increment invitation code usage
    code_obj.used_count += 1
    await db.flush()

    await db.commit()

    return JoinResponse(
        tenant=TenantOut.model_validate(tenant),
        role=final_role,
        access_token=access_token,
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

    sso_domain is stored as a full URL (e.g. "https://acme.clawith.ai" or "http://1.2.3.4:3009").
    The incoming `domain` parameter is the host (without protocol).

    Lookup precedence:
    1. Exact match on tenant.sso_domain ending with the host (strips protocol)
    2. Extract slug from "{slug}.clawith.ai" and match tenant.slug
    """
    tenant = None

    # 1. Match by stripping protocol from stored sso_domain
    # sso_domain = "https://acme.clawith.ai" → compare against "acme.clawith.ai"
    for proto in ("https://", "http://"):
        result = await db.execute(
            select(Tenant).where(Tenant.sso_domain == f"{proto}{domain}")
        )
        tenant = result.scalar_one_or_none()
        if tenant:
            break

    # 2. Try without port (e.g. domain = "1.2.3.4:3009" → try "1.2.3.4")
    if not tenant and ":" in domain:
        domain_no_port = domain.split(":")[0]
        for proto in ("https://", "http://"):
            result = await db.execute(
                select(Tenant).where(Tenant.sso_domain.like(f"{proto}{domain_no_port}%"))
            )
            tenant = result.scalar_one_or_none()
            if tenant:
                break

    # 3. Fallback: extract slug from subdomain pattern
    if not tenant:
        import re
        m = re.match(r"^([a-z0-9][a-z0-9\-]*[a-z0-9])\.clawith\.ai$", domain.lower())
        if m:
            slug = m.group(1)
            result = await db.execute(select(Tenant).where(Tenant.slug == slug))
            tenant = result.scalar_one_or_none()

    if not tenant or not tenant.is_active or not tenant.sso_enabled:
        raise HTTPException(status_code=404, detail="Tenant not found or not active or SSO not enabled")

    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "sso_enabled": tenant.sso_enabled,
        "sso_domain": tenant.sso_domain,
        "is_active": tenant.is_active,
    }

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
