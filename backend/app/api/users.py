import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


class UserQuotaUpdate(BaseModel):
    quota_message_limit: int | None = None
    quota_message_period: str | None = None
    quota_max_agents: int | None = None
    quota_agent_ttl_hours: int | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    # username/email/display_name can be None for SSO-created users whose Identity
    # was created without explicit values (e.g., DingTalk/Feishu OAuth flow).
    # The frontend should handle None gracefully.
    username: str | None = None
    email: str | None = None
    display_name: str | None = None
    primary_mobile: str | None = None
    role: str
    is_active: bool
    # Quota fields
    quota_message_limit: int
    quota_message_period: str
    quota_messages_used: int
    quota_max_agents: int
    quota_agent_ttl_hours: int
    # Computed
    agents_count: int = 0
    # Source info
    created_at: str | None = None
    source: str = 'registered'  # 'registered' | 'feishu' | 'dingtalk' | 'wecom' | etc.

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[UserOut])
async def list_users(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users in the specified tenant (admin only)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Platform admins can view any tenant; org_admins only their own
    tid = tenant_id if tenant_id and current_user.role == "platform_admin" else str(current_user.tenant_id)

    # Filter users by tenant — platform_admins only shown in their own tenant
    result = await db.execute(
        select(User).options(selectinload(User.identity)).where(
            User.tenant_id == tid
        ).order_by(User.created_at.asc())
    )
    users = result.scalars().all()

    out = []
    for u in users:
        # Count non-expired agents
        count_result = await db.execute(
            select(func.count()).select_from(Agent).where(
                Agent.creator_id == u.id,
                Agent.is_expired == False,
            )
        )
        agents_count = count_result.scalar() or 0

        user_dict = {
            "id": u.id,
            # Fallback to empty string if username/email/display_name is None to prevent
            # serialization errors for SSO-created users with incomplete Identity records.
            "username": u.username or u.email or f"{u.registration_source or 'user'}_{str(u.id)[:8]}",
            "email": u.email or "",
            "display_name": u.display_name or u.username or "",
            "primary_mobile": u.primary_mobile,
            "role": u.role,
            "is_active": u.is_active,
            "quota_message_limit": u.quota_message_limit,
            "quota_message_period": u.quota_message_period,
            "quota_messages_used": u.quota_messages_used,
            "quota_max_agents": u.quota_max_agents,
            "quota_agent_ttl_hours": u.quota_agent_ttl_hours,
            "agents_count": agents_count,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "source": (u.registration_source or 'registered'),
        }
        out.append(UserOut(**user_dict))
    return out


@router.patch("/{user_id}/quota", response_model=UserOut)
async def update_user_quota(
    user_id: uuid.UUID,
    data: UserQuotaUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's quota settings (admin only)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    result = await db.execute(
        select(User).options(selectinload(User.identity)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify users outside your organization")

    if data.quota_message_limit is not None:
        user.quota_message_limit = data.quota_message_limit
    if data.quota_message_period is not None:
        if data.quota_message_period not in ("permanent", "daily", "weekly", "monthly"):
            raise HTTPException(status_code=400, detail="Invalid period. Use: permanent, daily, weekly, monthly")
        user.quota_message_period = data.quota_message_period
    if data.quota_max_agents is not None:
        user.quota_max_agents = data.quota_max_agents
    if data.quota_agent_ttl_hours is not None:
        user.quota_agent_ttl_hours = data.quota_agent_ttl_hours

    await db.commit()
    await db.refresh(user)

    # Count agents
    count_result = await db.execute(
        select(func.count()).select_from(Agent).where(
            Agent.creator_id == user.id,
            Agent.is_expired == False,
        )
    )
    agents_count = count_result.scalar() or 0

    return UserOut(
        id=user.id, username=user.username, email=user.email,
        display_name=user.display_name, primary_mobile=user.primary_mobile,
        role=user.role, is_active=user.is_active,
        quota_message_limit=user.quota_message_limit,
        quota_message_period=user.quota_message_period,
        quota_messages_used=user.quota_messages_used,
        quota_max_agents=user.quota_max_agents,
        quota_agent_ttl_hours=user.quota_agent_ttl_hours,
        agents_count=agents_count,
    )


# ─── Role Management ───────────────────────────────────

class RoleUpdate(BaseModel):
    role: str


@router.patch("/{user_id}/role")
async def update_user_role(
    user_id: uuid.UUID,
    data: RoleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's role within the same company.

    Permissions:
    - org_admin: can set roles to org_admin / member within own tenant.
      Cannot assign platform_admin.
    - platform_admin: can set any valid role.

    Safety:
    - If the target is the ONLY remaining org_admin in the company,
      demoting them is blocked to prevent orphaned companies.
    """
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Validate target role value
    allowed_roles = ("org_admin", "member")
    if current_user.role == "platform_admin":
        allowed_roles = ("platform_admin", "org_admin", "member")
    if data.role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {', '.join(allowed_roles)}")

    # Find target user
    result = await db.execute(
        select(User).options(selectinload(User.identity)).where(User.id == user_id)
    )
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # org_admin can only modify users in the same tenant
    if current_user.role == "org_admin" and target_user.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify users outside your organization")

    # No-op shortcut
    if target_user.role == data.role:
        return {"status": "ok", "user_id": str(user_id), "role": data.role}

    # Last-admin protection: if demoting an org_admin, check they are not the only one
    if target_user.role in ("org_admin", "platform_admin") and data.role not in ("org_admin", "platform_admin"):
        admin_count_result = await db.execute(
            select(func.count()).select_from(User).where(
                User.tenant_id == target_user.tenant_id,
                User.role.in_(["org_admin", "platform_admin"]),
            )
        )
        admin_count = admin_count_result.scalar() or 0
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote the only administrator. Promote another user first."
            )

    target_user.role = data.role
    await db.commit()
    return {"status": "ok", "user_id": str(user_id), "role": data.role}


# ─── Profile Management ───────────────────────────────

class UserProfileUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    primary_mobile: str | None = None
    is_active: bool | None = None
    new_password: str | None = None


@router.patch("/{user_id}/profile", response_model=UserOut)
async def update_user_profile(
    user_id: uuid.UUID,
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's basic profile information.

    Permissions:
    - platform_admin: can edit any user.
    - org_admin: can only edit users within the same tenant.
    - Cannot edit platform_admin users (unless caller is platform_admin).
    """
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Fetch target user
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # org_admin can only edit users in the same tenant
    if current_user.role == "org_admin":
        if target.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=403, detail="Cannot modify users outside your organization")
        if target.role == "platform_admin":
            raise HTTPException(status_code=403, detail="Cannot modify platform admin users")

    # Update fields
    if data.display_name is not None:
        if not data.display_name.strip():
            raise HTTPException(status_code=400, detail="Display name cannot be empty")
        target.display_name = data.display_name.strip()
    if data.email is not None:
        email_val = data.email.strip().lower()
        if email_val:
            # Check email uniqueness (exclude self)
            existing = await db.execute(
                select(User).where(User.email == email_val, User.id != user_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Email already in use by another user")
            target.email = email_val
    if data.primary_mobile is not None:
        target.primary_mobile = data.primary_mobile.strip() or None
    if data.is_active is not None:
        target.is_active = data.is_active
    if data.new_password is not None and data.new_password.strip():
        from app.core.security import hash_password
        if len(data.new_password.strip()) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        target.password_hash = hash_password(data.new_password.strip())

    await db.commit()
    await db.refresh(target)

    # Count agents for response
    count_result = await db.execute(
        select(func.count()).select_from(Agent).where(
            Agent.creator_id == target.id,
            Agent.is_expired == False,
        )
    )
    agents_count = count_result.scalar() or 0

    return UserOut(
        id=target.id,
        username=target.username,
        email=target.email,
        display_name=target.display_name,
        primary_mobile=target.primary_mobile,
        role=target.role,
        is_active=target.is_active,
        quota_message_limit=target.quota_message_limit,
        quota_message_period=target.quota_message_period,
        quota_messages_used=target.quota_messages_used,
        quota_max_agents=target.quota_max_agents,
        quota_agent_ttl_hours=target.quota_agent_ttl_hours,
        agents_count=agents_count,
        created_at=target.created_at.isoformat() if target.created_at else None,
        source=target.registration_source or "registered",
    )
