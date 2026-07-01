"""RBAC permission checking utilities."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple

from fastapi import HTTPException, status
from sqlalchemy import false, or_, select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentPermission
from app.models.org import AgentAgentRelationship, AgentRelationship, OrgMember
from app.models.user import User


@dataclass(frozen=True)
class RosterVisibility:
    """Visibility result for roster-driven agent and human lookup."""

    visible: bool
    can_contact: bool
    unavailable_reason: str | None = None


def _agent_access_mode(agent: Agent) -> str:
    return getattr(agent, "access_mode", None) or "company"


def _agent_tenant_matches_user(agent: Agent, user: User) -> bool:
    agent_tenant_id = getattr(agent, "tenant_id", None)
    return agent_tenant_id is not None and agent_tenant_id == getattr(user, "tenant_id", None)


def _agent_tenant_matches_agent(source_agent: Agent, target_agent: Agent) -> bool:
    source_tenant_id = getattr(source_agent, "tenant_id", None)
    return source_tenant_id is not None and source_tenant_id == getattr(target_agent, "tenant_id", None)


def _non_private_mode(agent: Agent) -> bool:
    return _agent_access_mode(agent) != "private"


def can_use_agent_static(user: User, agent: Agent) -> bool:
    """Return whether a user can use an agent without DB-backed custom checks."""
    if not user or not agent:
        return False
    if not getattr(user, "is_active", True):
        return False
    if not _agent_tenant_matches_user(agent, user):
        return False
    if getattr(agent, "creator_id", None) == getattr(user, "id", None):
        return True
    access_mode = _agent_access_mode(agent)
    if access_mode == "company":
        return True
    if access_mode == "private":
        return False
    # custom access needs AgentPermission and must use can_use_agent().
    return False


async def can_use_agent(db: AsyncSession, user: User, agent: Agent) -> bool:
    """Return whether an active human user can use an agent under Directory rules."""
    if can_use_agent_static(user, agent):
        return True
    if not user or not agent:
        return False
    if not getattr(user, "is_active", True):
        return False
    if not _agent_tenant_matches_user(agent, user):
        return False

    access_mode = _agent_access_mode(agent)
    if access_mode != "custom":
        return False
    if _is_admin(user):
        return True

    result = await db.execute(
        select(AgentPermission.id).where(
            AgentPermission.agent_id == agent.id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == user.id,
            AgentPermission.access_level.in_(["use", "manage"]),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def can_manage_agent(db: AsyncSession, user: User, agent: Agent) -> bool:
    """Return whether a human user can manage agent configuration."""
    if not user or not agent:
        return False
    if not getattr(user, "is_active", True):
        return False
    if not _agent_tenant_matches_user(agent, user):
        return False
    if getattr(agent, "creator_id", None) == getattr(user, "id", None):
        return True

    access_mode = _agent_access_mode(agent)
    if _is_admin(user) and access_mode != "private":
        return True

    if access_mode == "custom":
        result = await db.execute(
            select(AgentPermission).where(
                AgentPermission.agent_id == agent.id,
                AgentPermission.scope_type == "user",
                AgentPermission.scope_id == user.id,
                AgentPermission.access_level == "manage",
            )
        )
        return result.scalar_one_or_none() is not None

    return False


def _roster_agent_unavailable_reason(agent: Agent) -> str | None:
    status_value = getattr(agent, "status", None)
    if status_value in (None, "running", "idle"):
        pass
    elif status_value == "stopped":
        return "agent_stopped"
    elif status_value == "error":
        return "agent_error"
    else:
        return f"agent_status_{status_value}"
    if is_agent_expired(agent):
        return "agent_expired"
    return None


def evaluate_roster_agent_visibility(
    source_agent: Agent,
    target_agent: Agent,
    *,
    authorized_custom_target: bool = False,
) -> RosterVisibility:
    """Evaluate whether source can see and currently contact target in Directory."""
    if not source_agent or not target_agent:
        return RosterVisibility(False, False)
    if getattr(source_agent, "id", None) == getattr(target_agent, "id", None):
        return RosterVisibility(False, False)
    if not _agent_tenant_matches_agent(source_agent, target_agent):
        return RosterVisibility(False, False)

    source_mode = _agent_access_mode(source_agent)
    target_mode = _agent_access_mode(target_agent)
    visible = False

    if source_mode == "private":
        visible = (
            target_mode == "private"
            and getattr(source_agent, "creator_id", None) == getattr(target_agent, "creator_id", None)
        )
    else:
        visible = target_mode == "company" or (target_mode == "custom" and authorized_custom_target)

    if not visible:
        return RosterVisibility(False, False)

    unavailable_reason = _roster_agent_unavailable_reason(target_agent)
    return RosterVisibility(True, unavailable_reason is None, unavailable_reason)


def evaluate_roster_human_visibility(
    source_agent: Agent,
    member: OrgMember,
    *,
    authorized_custom_human: bool = False,
) -> RosterVisibility:
    """Evaluate whether source can see and currently contact a human org member."""
    if not source_agent or not member:
        return RosterVisibility(False, False)
    source_tenant_id = getattr(source_agent, "tenant_id", None)
    member_tenant_id = getattr(member, "tenant_id", None)
    if not source_tenant_id or source_tenant_id != member_tenant_id:
        return RosterVisibility(False, False)

    source_mode = _agent_access_mode(source_agent)
    if source_mode == "private":
        visible = getattr(member, "user_id", None) == getattr(source_agent, "creator_id", None)
    elif source_mode == "custom":
        visible = authorized_custom_human
    else:
        visible = True

    if not visible:
        return RosterVisibility(False, False)

    if getattr(member, "status", None) != "active":
        return RosterVisibility(True, False, "member_inactive")

    return RosterVisibility(True, True, None)


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

    visible_conditions = [
        Agent.creator_id == user.id,
        Agent.access_mode == "company",
    ]
    if _is_admin(user):
        visible_conditions.append(Agent.access_mode == "custom")
    else:
        visible_conditions.append(
            exists().where(
                AgentPermission.agent_id == Agent.id,
                AgentPermission.scope_type == "user",
                AgentPermission.scope_id == user.id,
                AgentPermission.access_level.in_(["use", "manage"]),
            )
        )

    return stmt.where(
        Agent.tenant_id == target_tenant_id,
        or_(*visible_conditions),
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

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    if agent.tenant_id != user.tenant_id:
        return None
    if agent.creator_id == user.id:
        return "manage"

    if await can_manage_agent(db, user, agent):
        return "manage"
    if await can_use_agent(db, user, agent):
        return "use"
    return None


async def user_can_manage_agent_id(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    agent: Agent,
) -> bool:
    return (await get_agent_access_level_for_user_id(db, user_id, agent)) == "manage"


async def get_agent_accessible_user_ids(db: AsyncSession, agent: Agent) -> set[uuid.UUID]:
    """Return platform users who can access an agent under current policy."""
    ids: set[uuid.UUID] = set()
    if agent.creator_id:
        ids.add(agent.creator_id)

    access_mode = _agent_access_mode(agent)
    if access_mode == "company":
        result = await db.execute(
            select(User.id).where(
                User.tenant_id == agent.tenant_id,
                User.is_active == True,  # noqa: E712
            )
        )
        ids.update(row[0] for row in result.fetchall())
        return ids

    if access_mode == "custom":
        admin_result = await db.execute(
            select(User.id).where(
                User.tenant_id == agent.tenant_id,
                User.is_active == True,  # noqa: E712
                User.role.in_(["platform_admin", "org_admin"]),
            )
        )
        ids.update(row[0] for row in admin_result.fetchall())

        perm_result = await db.execute(
            select(AgentPermission.scope_id).where(
                AgentPermission.agent_id == agent.id,
                AgentPermission.scope_type == "user",
                AgentPermission.scope_id.is_not(None),
                AgentPermission.access_level.in_(["use", "manage"]),
            )
        )
        ids.update(row[0] for row in perm_result.fetchall() if row[0])
        return ids

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
    source_result = await db.execute(select(Agent).where(Agent.id == rel.agent_id))
    source = source_result.scalar_one_or_none()
    target = rel.__dict__.get("target_agent")
    if target is None:
        target_result = await db.execute(select(Agent).where(Agent.id == rel.target_agent_id))
        target = target_result.scalar_one_or_none()

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
        source_result = await db.execute(select(Agent).where(Agent.id == rel.agent_id))
        source_agent = source_result.scalar_one_or_none()
    member = rel.__dict__.get("member")
    if member is None:
        member_result = await db.execute(select(OrgMember).where(OrgMember.id == rel.member_id))
        member = member_result.scalar_one_or_none()

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

    if await can_manage_agent(db, user, agent):
        return agent, "manage"
    if await can_use_agent(db, user, agent):
        return agent, "use"

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


def can_auto_contact_company_agent(source_agent: Agent, target_agent: Agent) -> bool:
    """Return whether source can contact target via the phase-1 company-agent rule."""
    if not source_agent or not target_agent:
        return False
    if getattr(source_agent, "id", None) == getattr(target_agent, "id", None):
        return False
    source_tenant_id = getattr(source_agent, "tenant_id", None)
    target_tenant_id = getattr(target_agent, "tenant_id", None)
    if not source_tenant_id or source_tenant_id != target_tenant_id:
        return False
    if getattr(target_agent, "access_mode", None) != "company":
        return False
    target_status = getattr(target_agent, "status", None)
    if target_status and target_status not in ("running", "idle"):
        return False
    if is_agent_expired(target_agent):
        return False
    return True
