"""Organization management API routes (users only)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin, get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.schemas import UserOut, UserUpdate

router = APIRouter(prefix="/org", tags=["organization"])


# ─── Users Management ──────────────────────────────────

@router.get("/users", response_model=list[UserOut])
async def list_users(
    tenant_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List users, optionally filtered by tenant."""
    query = select(User).where(User.is_active == True)

    target_tenant_id = current_user.tenant_id
    if current_user.role in ("platform_admin", "org_admin") and tenant_id:
        target_tenant_id = tenant_id
    if target_tenant_id:
        query = query.where(User.tenant_id == target_tenant_id)

    query = query.order_by(User.display_name)
    result = await db.execute(query)
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}", response_model=UserOut)
async def admin_update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin update user profile."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = data.model_dump(exclude_unset=True)

    # Validate email uniqueness within tenant if changing
    if "email" in update_data and update_data["email"] != user.email:
        existing = await db.execute(
            select(User).where(
                User.email.ilike(update_data["email"]),
                User.tenant_id == user.tenant_id,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    # Validate mobile uniqueness within tenant if changing
    if "primary_mobile" in update_data and update_data["primary_mobile"] != user.primary_mobile:
        existing = await db.execute(
            select(User).where(
                User.primary_mobile == update_data["primary_mobile"],
                User.tenant_id == user.tenant_id,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Mobile already registered")

    for field, value in update_data.items():
        setattr(user, field, value)
    await db.flush()

    # Sync email/phone to OrgMember if changed
    if "email" in update_data or "primary_mobile" in update_data:
        from app.services.registration_service import registration_service
        await registration_service.sync_org_member_contact_from_user(
            db,
            user,
            sync_email="email" in update_data,
            sync_phone="primary_mobile" in update_data,
        )

    return UserOut.model_validate(user)
