"""User management API — admin-only user listing and quota management."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

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
    username: str
    email: str
    display_name: str
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

    # Include platform_admin users in every tenant's list (they belong to all companies)
    from sqlalchemy import or_
    result = await db.execute(
        select(User).where(
            or_(User.tenant_id == tid, User.role == "platform_admin")
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
            "username": u.username,
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role,
            "is_active": u.is_active,
            "quota_message_limit": u.quota_message_limit,
            "quota_message_period": u.quota_message_period,
            "quota_messages_used": u.quota_messages_used,
            "quota_max_agents": u.quota_max_agents,
            "quota_agent_ttl_hours": u.quota_agent_ttl_hours,
            "agents_count": agents_count,
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

    result = await db.execute(select(User).where(User.id == user_id))
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
        display_name=user.display_name, role=user.role, is_active=user.is_active,
        quota_message_limit=user.quota_message_limit,
        quota_message_period=user.quota_message_period,
        quota_messages_used=user.quota_messages_used,
        quota_max_agents=user.quota_max_agents,
        quota_agent_ttl_hours=user.quota_agent_ttl_hours,
        agents_count=agents_count,
    )
