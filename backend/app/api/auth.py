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

    The first user to register becomes the platform admin automatically and is
    assigned to the default company as org_admin. Subsequent users register
    without a company — they must create or join one via /tenants/self-create
    or /tenants/join.
    """
    # Check existing
    existing = await db.execute(
        select(User).where((User.username == data.username) | (User.email == data.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists")

    # Check if this is the first user (→ platform admin + default company org_admin)
    from sqlalchemy import func
    user_count = await db.execute(select(func.count()).select_from(User))
    is_first_user = user_count.scalar() == 0

    # Note: invitation code validation has been moved to the company-join flow
    # (POST /tenants/join). Registration itself is now open.

    # Resolve tenant and role for first user only
    tenant_uuid = None
    role = "member"
    quota_defaults: dict = {}

    if is_first_user:
        from app.models.tenant import Tenant
        default = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = default.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name="Default", slug="default", im_provider="web_only")
            db.add(tenant)
            await db.flush()
        tenant_uuid = tenant.id
        role = "platform_admin"
        quota_defaults = {
            "quota_message_limit": tenant.default_message_limit,
            "quota_message_period": tenant.default_message_period,
            "quota_max_agents": tenant.default_max_agents,
            "quota_agent_ttl_hours": tenant.default_agent_ttl_hours,
        }

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        role=role,
        tenant_id=tenant_uuid,
        **quota_defaults,
    )
    db.add(user)
    await db.flush()

    # Auto-create Participant identity for the new user
    from app.models.participant import Participant
    db.add(Participant(
        type="user", ref_id=user.id,
        display_name=user.display_name, avatar_url=user.avatar_url,
    ))
    await db.flush()

    # Seed default agents after first user (platform admin) registration
    if is_first_user:
        await db.commit()  # commit user first so seeder can find the admin
        try:
            from app.services.agent_seeder import seed_default_agents
            await seed_default_agents()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to seed default agents: {e}")

    needs_setup = tenant_uuid is None
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        needs_company_setup=needs_setup,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login with username and password."""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # Check if user's company is disabled
    if user.tenant_id:
        from app.models.tenant import Tenant
        t_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant and not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your company has been disabled. Please contact the platform administrator.",
            )

    needs_setup = user.tenant_id is None
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        needs_company_setup=needs_setup,
    )


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
    update_data = data.model_dump(exclude_unset=True)

    # Validate username uniqueness if changing
    if "username" in update_data and update_data["username"] != current_user.username:
        existing = await db.execute(select(User).where(User.username == update_data["username"]))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")

    for field, value in update_data.items():
        setattr(current_user, field, value)
    await db.flush()
    return UserOut.model_validate(current_user)


@router.put("/me/password")
async def change_password(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change current user's password. Requires old_password verification."""
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="Both old_password and new_password are required")

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    if not verify_password(old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.password_hash = hash_password(new_password)
    await db.flush()
    return {"ok": True}
