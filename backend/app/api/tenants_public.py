"""Tenant (Company) creation during registration - public endpoint."""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def generate_slug(name: str) -> str:
    """Generate a URL-friendly slug from company name."""
    # Convert to lowercase, replace spaces and special chars with hyphens
    slug = re.sub(r'[^a-z0-9\s-]', '', name.lower())
    slug = re.sub(r'[\s-]+', '-', slug)
    # Add random suffix to ensure uniqueness
    return f"{slug}-{uuid.uuid4().hex[:6]}"


class TenantCreatePublic(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    im_provider: str
    timezone: str = "UTC"
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/public/create", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant_public(
    data: TenantCreatePublic,
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant/company during registration (public, no auth required).
    
    This endpoint allows users to create a new company when registering,
    eliminating the need for a pre-existing company to select from.
    """
    # Generate a unique slug
    slug = generate_slug(data.name)
    
    # Check if slug exists (unlikely with random suffix, but be safe)
    for _ in range(10):  # Try up to 10 times
        existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = generate_slug(data.name)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique company ID"
        )

    tenant = Tenant(name=data.name, slug=slug, im_provider="web_only")
    db.add(tenant)
    await db.flush()
    return TenantOut.model_validate(tenant)
