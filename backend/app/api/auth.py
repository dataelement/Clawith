"""Authentication API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.schemas import TokenResponse, UserLogin, UserOut, UserRegister, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/registration-config")
async def get_registration_config(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns registration requirements (no auth needed)."""
    from app.models.system_settings import SystemSetting
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "invitation_code_enabled"))
    setting = result.scalar_one_or_none()
    enabled = setting.value.get("enabled", False) if setting else False
    return {"invitation_code_required": enabled}


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user account.

    The first user to register becomes the platform admin automatically.
    """
    # Check existing
    existing = await db.execute(
        select(User).where((User.username == data.username) | (User.email == data.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists")

    # Check if this is the first user (→ platform admin)
    from sqlalchemy import func
    user_count = await db.execute(select(func.count()).select_from(User))
    is_first_user = user_count.scalar() == 0

    # Resolve tenant — required; fall back to default if not provided
    from app.models.tenant import Tenant
    tenant_uuid = None
    if data.tenant_id:
        t_result = await db.execute(select(Tenant).where(Tenant.id == uuid.UUID(data.tenant_id)))
        tenant = t_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=400, detail="选择的公司不存在")
        tenant_uuid = tenant.id
    else:
        # Auto-assign to the default company
        default = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = default.scalar_one_or_none()
        if tenant:
            tenant_uuid = tenant.id

    # ── Invitation code check ──
    from app.models.system_settings import SystemSetting
    inv_setting = await db.execute(select(SystemSetting).where(SystemSetting.key == "invitation_code_enabled"))
    inv_s = inv_setting.scalar_one_or_none()
    invitation_required = inv_s.value.get("enabled", False) if inv_s else False

    invitation_code_obj = None
    if invitation_required:
        if not data.invitation_code:
            raise HTTPException(status_code=400, detail="Invitation code is required")
        from app.models.invitation_code import InvitationCode
        ic_result = await db.execute(
            select(InvitationCode).where(InvitationCode.code == data.invitation_code, InvitationCode.is_active == True)
        )
        invitation_code_obj = ic_result.scalar_one_or_none()
        if not invitation_code_obj:
            raise HTTPException(status_code=400, detail="Invalid invitation code")
        if invitation_code_obj.used_count >= invitation_code_obj.max_uses:
            raise HTTPException(status_code=400, detail="Invitation code has reached its usage limit")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        role="platform_admin" if is_first_user else "member",
        tenant_id=tenant_uuid,
        # Inherit quota defaults from tenant
        quota_message_limit=tenant.default_message_limit if tenant else 50,
        quota_message_period=tenant.default_message_period if tenant else "permanent",
        quota_max_agents=tenant.default_max_agents if tenant else 2,
        quota_agent_ttl_hours=tenant.default_agent_ttl_hours if tenant else 48,
    )
    db.add(user)
    await db.flush()

    # Increment invitation code usage
    if invitation_code_obj:
        invitation_code_obj.used_count += 1

    # Seed default agents after first user (platform admin) registration
    if is_first_user:
        await db.commit()  # commit user first so seeder can find the admin
        try:
            from app.services.agent_seeder import seed_default_agents
            await seed_default_agents()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to seed default agents: {e}")

    token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login with username and password."""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserOut.model_validate(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user profile."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    await db.flush()
    return UserOut.model_validate(current_user)
