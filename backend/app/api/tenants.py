"""Tenant (Company) management API — platform_admin only."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_role
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9_-]+$")
    im_provider: str = "web_only"


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


@router.get("/", response_model=list[TenantOut])
async def list_tenants(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants (platform_admin only)."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return [TenantOut.model_validate(t) for t in result.scalars().all()]


@router.post("/", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    data: TenantCreate,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant/company (platform_admin only)."""
    # Check slug uniqueness
    existing = await db.execute(select(Tenant).where(Tenant.slug == data.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Slug '{data.slug}' already exists")

    tenant = Tenant(name=data.name, slug=data.slug, im_provider=data.im_provider)
    db.add(tenant)
    await db.flush()
    return TenantOut.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get tenant details."""
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


class TenantSimple(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    model_config = {"from_attributes": True}


@router.get("/public/list", response_model=list[TenantSimple])
async def list_tenants_public(db: AsyncSession = Depends(get_db)):
    """List active tenants for registration page (no auth required)."""
    result = await db.execute(
        select(Tenant).where(Tenant.is_active == True).order_by(Tenant.name)
    )
    return [TenantSimple.model_validate(t) for t in result.scalars().all()]
