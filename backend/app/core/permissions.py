"""RBAC permission checking utilities."""

import uuid
from datetime import datetime, timezone
from typing import Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import agent_access_dao
from app.database import bind_session_context
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentAgentRelationship, AgentRelationship
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

    if user.role in ("platform_admin", "org_admin"):
        return stmt.where(
            Agent.tenant_id == target_tenant_id,
            or_(
                Agent.creator_id == user.id,
                Agent.access_mode != "private",
            ),
        )

    explicit_user_ids = (
        select(AgentPermission.agent_id)
        .where(
            and_(
                AgentPermission.scope_type == "user",
                AgentPermission.scope_id == user.id,
            )
        )
    )

    return stmt.where(
        Agent.tenant_id == target_tenant_id,
        or_(
            Agent.creator_id == user.id,
            Agent.access_mode == "company",
            Agent.id.in_(explicit_user_ids),
        ),
    )


def is_company_visible_agent(agent: Agent) -> bool:
    """Return whether an agent participates in company-public surfaces."""
    return (getattr(agent, "access_mode", None) or "company") == "company"


def _is_admin(user: User) -> bool:
    return user.role in ("platform_admin", "org_admin")


async def get_agent_access_level_for_user_id(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    agent: Agent,
) -> str | None:
    """Return 'manage', 'use', or None for a platform user and an agent.

    This helper is intentionally HTTP-exception free so background jobs, gateway
    calls, and relationship status checks can reuse the same access semantics.
    """
    if not user_id:
        return None

    async with bind_session_context(db):
        user = await agent_access_dao.get_user(user_id)
    if not user or not user.is_active:
        return None
    if agent.tenant_id != user.tenant_id:
        return None
    if agent.creator_id == user.id:
        return "manage"

    access_mode = getattr(agent, "access_mode", None) or "company"
    if _is_admin(user) and access_mode != "private":
        return "manage"

    async with bind_session_context(db):
        permissions = await agent_access_dao.list_permissions(agent.id)

    if access_mode == "company":
        company_level = getattr(agent, "company_access_level", None) or next(
            (perm.access_level for perm in permissions if perm.scope_type == "company"),
            "use",
        )
        return company_level or "use"

    if access_mode == "custom":
        for perm in permissions:
            if perm.scope_type == "user" and perm.scope_id == user.id:
                return perm.access_level or "use"

    return None


async def user_can_manage_agent_id(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    agent: Agent,
) -> bool:
    return (await get_agent_access_level_for_user_id(db, user_id, agent)) == "manage"


async def get_agent_accessible_user_ids(db: AsyncSession, agent: Agent) -> set[uuid.UUID]:
    """Return platform users who can access an agent under current policy."""
    access_mode = getattr(agent, "access_mode", None) or "company"
    ids: set[uuid.UUID] = set()
    if agent.creator_id:
        ids.add(agent.creator_id)

    if access_mode == "company":
        async with bind_session_context(db):
            ids.update(await agent_access_dao.list_active_user_ids_by_tenant(agent.tenant_id))
        return ids

    if access_mode == "custom":
        async with bind_session_context(db):
            ids.update(await agent_access_dao.list_custom_permission_user_ids(agent.id))
            ids.update(await agent_access_dao.list_active_admin_user_ids_by_tenant(agent.tenant_id))

    return ids


def _agent_available(agent: Agent | None) -> tuple[bool, str | None]:
    if not agent:
        return False, "target_not_found"
    if getattr(agent, "status", None) in ("stopped", "error"):
        return False, f"target_status_{agent.status}"
    if is_agent_expired(agent):
        return False, "target_expired"
    return True, None


async def evaluate_agent_relationship_status(
    db: AsyncSession,
    rel: AgentAgentRelationship,
    *,
    current_user_id: uuid.UUID | None = None,
) -> dict:
    """Compute the effective status for an Agent -> Agent relationship."""
    async with bind_session_context(db):
        source = await agent_access_dao.get_agent(rel.agent_id)
    target = rel.__dict__.get("target_agent")
    if target is None:
        async with bind_session_context(db):
            target = await agent_access_dao.get_agent(rel.target_agent_id)

    if not source or not target:
        return {
            "access_allowed": False,
            "access_status": "missing_target",
            "access_status_reason": "source_or_target_not_found",
        }
    if source.tenant_id != target.tenant_id:
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": "different_tenant",
        }

    available, reason = _agent_available(target)
    if not available:
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": reason or "target_unavailable",
        }

    created_by_user_id = getattr(rel, "created_by_user_id", None)
    if created_by_user_id:
        if await user_can_manage_agent_id(db, created_by_user_id, source) and await user_can_manage_agent_id(db, created_by_user_id, target):
            return {
                "access_allowed": True,
                "access_status": "active",
                "access_status_reason": None,
            }
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": "relationship_creator_no_longer_manages_both_agents",
        }

    target_mode = getattr(target, "access_mode", None) or "company"
    if target_mode == "company":
        return {
            "access_allowed": True,
            "access_status": "active",
            "access_status_reason": None,
        }

    candidate_user_ids = [
        current_user_id,
        source.creator_id,
    ]
    seen: set[uuid.UUID] = set()
    for user_id in candidate_user_ids:
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        if await user_can_manage_agent_id(db, user_id, source) and await user_can_manage_agent_id(db, user_id, target):
            return {
                "access_allowed": True,
                "access_status": "active",
                "access_status_reason": None,
            }

    return {
        "access_allowed": False,
        "access_status": "restricted",
        "access_status_reason": "manager_no_longer_has_access_to_both_agents",
    }


async def evaluate_human_relationship_status(
    db: AsyncSession,
    rel: AgentRelationship,
    *,
    source_agent: Agent | None = None,
) -> dict:
    """Compute the effective status for an Agent -> Human relationship."""
    if source_agent is None:
        async with bind_session_context(db):
            source_agent = await agent_access_dao.get_agent(rel.agent_id)
    member = rel.__dict__.get("member")
    if member is None:
        async with bind_session_context(db):
            member = await agent_access_dao.get_org_member(rel.member_id)

    if not source_agent or not member:
        return {
            "access_allowed": False,
            "access_status": "missing_target",
            "access_status_reason": "agent_or_member_not_found",
        }
    if member.status != "active":
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": "member_inactive",
        }
    if member.tenant_id and source_agent.tenant_id and member.tenant_id != source_agent.tenant_id:
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": "different_tenant",
        }
    if member.user_id:
        access_level = await get_agent_access_level_for_user_id(db, member.user_id, source_agent)
        if not access_level:
            return {
                "access_allowed": False,
                "access_status": "restricted",
                "access_status_reason": "platform_user_no_agent_access",
            }

    return {
        "access_allowed": True,
        "access_status": "active",
        "access_status_reason": None,
    }


async def check_agent_access(db: AsyncSession, user: User, agent_id: uuid.UUID) -> Tuple[Agent, str]:
    """Check if a user has access to a specific agent.

    Returns (agent, access_level) where access_level is 'manage' or 'use'.

    Access is granted if:
    1. User is the agent creator -> manage
    2. Company admin + non-private agent -> manage
    3. User has explicit permission (company/user scope) -> from permission record
    """
    async with bind_session_context(db):
        agent = await agent_access_dao.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Tenant isolation applies to all users.
    if agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this agent")

    # Creator always has manage access
    if agent.creator_id == user.id:
        return agent, "manage"

    access_mode = getattr(agent, "access_mode", None) or "company"

    async with bind_session_context(db):
        permissions = await agent_access_dao.list_permissions(agent_id)

    is_admin = user.role in ("platform_admin", "org_admin")
    if is_admin and access_mode != "private":
        return agent, "manage"

    if access_mode == "company":
        company_level = getattr(agent, "company_access_level", None)
        if not company_level:
            company_level = next(
                (perm.access_level for perm in permissions if perm.scope_type == "company"),
                "use",
            )
        return agent, company_level or "use"

    if access_mode == "custom":
        for perm in permissions:
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
