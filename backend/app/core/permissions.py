"""RBAC permission checking utilities."""

import uuid
from datetime import datetime, timezone
from typing import Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentPermission
from app.models.user import User


def build_visible_agents_query(
    user: User,
    *,
    tenant_id: uuid.UUID | None = None,
):
    """Build a query for agents visible to the current user.

    Visibility defaults to "same company + creator/self-permitted/company-wide".
    Company admins can see all non-private agents in their tenant. Private
    user-only agents stay hidden unless the admin created them.
    """
    stmt = select(Agent)

    target_tenant_id = tenant_id if tenant_id is not None else user.tenant_id
    if target_tenant_id is None:
        return stmt.where(false())

    public_or_shared_ids = (
        select(AgentPermission.agent_id)
        .where(AgentPermission.scope_type != "user")
    )
    private_user_only_ids = (
        select(AgentPermission.agent_id)
        .where(AgentPermission.scope_type == "user")
        .where(AgentPermission.agent_id.not_in(public_or_shared_ids))
    )

    if user.role in ("platform_admin", "org_admin"):
        return stmt.where(
            Agent.tenant_id == target_tenant_id,
            or_(
                Agent.creator_id == user.id,
                Agent.id.not_in(private_user_only_ids),
            ),
        )

    permitted_ids = (
        select(AgentPermission.agent_id)
        .where(
            or_(
                AgentPermission.scope_type == "company",
                and_(
                    AgentPermission.scope_type == "user",
                    AgentPermission.scope_id == user.id,
                ),
            )
        )
    )

    return stmt.where(
        Agent.tenant_id == target_tenant_id,
        or_(
            Agent.creator_id == user.id,
            Agent.id.in_(permitted_ids),
        ),
    )


async def check_agent_access(db: AsyncSession, user: User, agent_id: uuid.UUID) -> Tuple[Agent, str]:
    """Check if a user has access to a specific agent.

    Returns (agent, access_level) where access_level is 'manage' or 'use'.

    Access is granted if:
    1. User is the agent creator -> manage
    2. Company admin + non-private agent -> manage
    3. User has explicit permission (company/user scope) -> from permission record
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Tenant isolation applies to all users.
    if agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this agent")

    # Creator always has manage access
    if agent.creator_id == user.id:
        return agent, "manage"

    # Check permission scopes
    perms = await db.execute(select(AgentPermission).where(AgentPermission.agent_id == agent_id))
    permissions = perms.scalars().all()

    is_admin = user.role in ("platform_admin", "org_admin")
    is_private_user_only = bool(permissions) and all(perm.scope_type == "user" for perm in permissions)
    if is_admin and not is_private_user_only:
        return agent, "manage"

    for perm in permissions:
        if perm.scope_type == "company" and agent.tenant_id == user.tenant_id:
            return agent, perm.access_level or "use"
        if perm.scope_type == "user" and perm.scope_id == user.id:
            return agent, perm.access_level or "use"

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this agent")


def is_agent_creator(user: User, agent: Agent) -> bool:
    """Check if the user is the creator (admin) of the agent."""
    return agent.creator_id == user.id


def is_agent_expired(agent: Agent) -> bool:
    """Return True if the agent is manually marked expired or its expires_at is in the past."""
    if getattr(agent, 'is_expired', False):
        return True
    expires_at = getattr(agent, 'expires_at', None)
    if expires_at and datetime.now(timezone.utc) > expires_at:
        return True
    return False
