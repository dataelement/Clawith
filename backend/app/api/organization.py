from typing import Any
"""Organization management API routes (users only)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import query_dao
from app.core.security import get_current_admin, get_current_user
from app.database import get_db
from app.models.user import User, Identity
from app.schemas.schemas import UserOut, UserUpdate

from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/org", tags=["organization"])


def _is_platform_admin(user: User) -> bool:
    """Return whether the caller has platform-wide administrative authority."""
    return user.role == "platform_admin" or bool(getattr(user.identity, "is_platform_admin", False))


# ─── Users Management ──────────────────────────────────

@router.get("/users", response_model=list[UserOut])
async def list_users(
    tenant_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Any = None,
):
    """List users, optionally filtered by tenant."""
    query = (
        select(User)
        .options(selectinload(User.identity))
        .where(User.is_active)
    )

    target_tenant_id = current_user.tenant_id
    if _is_platform_admin(current_user) and tenant_id:
        target_tenant_id = tenant_id
    if target_tenant_id:
        query = query.where(User.tenant_id == target_tenant_id)

    query = query.order_by(User.display_name)
    result = await query_dao.execute(db, query)
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}", response_model=UserOut)
async def admin_update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    current_user: User = Depends(get_current_admin),
    db: Any = None,
):
    """Admin update user profile."""
    query = (
        select(User)
        .options(selectinload(User.identity))
        .where(User.id == user_id)
    )
    if not _is_platform_admin(current_user):
        query = query.where(User.tenant_id == current_user.tenant_id)

    result = await query_dao.execute(db, query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = data.model_dump(exclude_unset=True)

    # Email is stored on the globally shared Identity rather than the tenant User.
    # An organization administrator must not be able to alter another member's
    # login and password-reset address, even if that member belongs to this tenant.
    if (
        "email" in update_data
        and not _is_platform_admin(current_user)
        and user.identity_id != current_user.identity_id
    ):
        raise HTTPException(status_code=403, detail="Cannot modify another user's login email")

    # Validate email uniqueness within tenant if changing
    if "email" in update_data and update_data["email"] != user.email:
        existing = await query_dao.execute(db, 
            select(User)
            .join(Identity, User.identity_id == Identity.id)
            .where(
                Identity.email == update_data["email"],
                User.tenant_id == user.tenant_id,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    # Validate mobile uniqueness within tenant if changing
    if "primary_mobile" in update_data and update_data["primary_mobile"] != user.primary_mobile:
        existing = await query_dao.execute(db, 
            select(User)
            .join(Identity, User.identity_id == Identity.id)
            .where(
                Identity.phone == update_data["primary_mobile"],
                User.tenant_id == user.tenant_id,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Mobile already registered")

    for field, value in update_data.items():
        setattr(user, field, value)
    await query_dao.flush(db)

    # Sync email/phone to OrgMember if changed
    if "email" in update_data or "primary_mobile" in update_data:
        from app.services.registration_service import registration_service
        await registration_service.sync_org_member_contact_from_user(
            user,
            sync_email="email" in update_data,
            sync_phone="primary_mobile" in update_data,
        )

    return UserOut.model_validate(user)
