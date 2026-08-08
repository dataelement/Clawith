"""RBAC permission checking utilities."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Tuple

from fastapi import HTTPException, status
from sqlalchemy import false, or_, select, exists

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


def _is_admin(user: User) -> bool:
    return user.role in ("platform_admin", "org_admin")


def can_use_agent_static(user: User, agent: Agent) -> bool:
    """Return whether a user can use an agent without DB-backed custom checks."""
    if not user or not agent:
        return False
    if getattr(agent, "deleted_at", None) is not None:
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


async def can_use_agent(
    user_or_db: Any,
    agent_or_user: Any,
    agent: Agent | None = None,
) -> bool:
    """Return whether an active human user can use an agent under Directory rules.

    Supports both ``can_use_agent(user, agent)`` and legacy ``can_use_agent(db, user, agent)``.
    """
    from app.dao.agent_dao import agent_dao

    if agent is not None:
        user, target_agent = agent_or_user, agent
    else:
        user, target_agent = user_or_db, agent_or_user

    if can_use_agent_static(user, target_agent):
        return True
    if not user or not target_agent:
        return False
    if getattr(target_agent, "deleted_at", None) is not None:
        return False
    if not getattr(user, "is_active", True):
        return False
    if not _agent_tenant_matches_user(target_agent, user):
        return False

    access_mode = _agent_access_mode(target_agent)
    if access_mode != "custom":
        return False
    if _is_admin(user):
        return True

    perm = await agent_dao.get_user_permission(target_agent.id, user.id)
    return perm is not None and perm.access_level in ("use", "manage")


async def can_manage_agent(
    user_or_db: Any,
    agent_or_user: Any,
    agent: Agent | None = None,
    *,
    include_deleted: bool = False,
) -> bool:
    """Return whether a human user can manage agent configuration.

    Supports both ``can_manage_agent(user, agent)`` and legacy ``can_manage_agent(db, user, agent)``.
    """
    from app.dao.agent_dao import agent_dao

    if agent is not None:
        user, target_agent = agent_or_user, agent
    else:
        user, target_agent = user_or_db, agent_or_user

    if not user or not target_agent:
        return False
    if not include_deleted and getattr(target_agent, "deleted_at", None) is not None:
        return False
    if not getattr(user, "is_active", True):
        return False
    if not _agent_tenant_matches_user(target_agent, user):
        return False
    if getattr(target_agent, "creator_id", None) == getattr(user, "id", None):
        return True

    access_mode = _agent_access_mode(target_agent)
    if _is_admin(user) and access_mode != "private":
        return True

    if access_mode == "custom":
        perm = await agent_dao.get_user_permission(target_agent.id, user.id)
        return perm is not None and perm.access_level == "manage"

    return False


def _roster_agent_unavailable_reason(agent: Agent) -> str | None:
    if getattr(agent, "deleted_at", None) is not None:
        return "agent_deleted"
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
    """Build a SQLAlchemy query for agents visible to the current user.

    This returns a query object for use in API-level pagination without executing it.
    Visibility: creator OR company-mode OR (custom + explicit permission / admin).
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
        Agent.deleted_at.is_(None),
        or_(*visible_conditions),
    )


def is_company_visible_agent(agent: Agent) -> bool:
    """Return whether an agent participates in company-public surfaces."""
    return (getattr(agent, "access_mode", None) or "company") == "company"


async def get_agent_access_level_for_user_id(
    user_id_or_db: Any,
    agent_or_user_id: Any,
    agent: Agent | None = None,
) -> str | None:
    """Return 'manage', 'use', or None for a platform user and an agent.

    Supports both ``get_agent_access_level_for_user_id(user_id, agent)`` and legacy with ``db``.
    """
    from app.dao.user_dao import user_dao

    if agent is not None:
        user_id, target_agent = agent_or_user_id, agent
    else:
        user_id, target_agent = user_id_or_db, agent_or_user_id

    if not user_id:
        return None

    user = await user_dao.get(user_id)
    if not user or not user.is_active:
        return None
    if target_agent.tenant_id != user.tenant_id:
        return None
    if target_agent.creator_id == user.id:
        return "manage"

    if await can_manage_agent(user, target_agent):
        return "manage"
    if await can_use_agent(user, target_agent):
        return "use"
    return None


async def user_can_manage_agent_id(
    user_id_or_db: Any,
    agent_or_user_id: Any,
    agent: Agent | None = None,
) -> bool:
    """Return whether a platform user can manage an agent by ID."""
    return (await get_agent_access_level_for_user_id(user_id_or_db, agent_or_user_id, agent)) == "manage"


async def get_agent_accessible_user_ids(
    agent_or_db: Any,
    agent: Agent | None = None,
) -> set[uuid.UUID]:
    """Return platform users who can access an agent under current policy."""
    from app.dao.agent_dao import agent_dao

    target_agent = agent if agent is not None else agent_or_db

    ids: set[uuid.UUID] = set()
    if target_agent.creator_id:
        ids.add(target_agent.creator_id)

    access_mode = _agent_access_mode(target_agent)
    if access_mode in ("company", "custom"):
        # arch-guard: allow (admin cross-tenant query scoped by agent.tenant_id)
        async with agent_dao.session(readonly=True) as db:
            if access_mode == "company":
                result = await db.execute(
                    select(User.id).where(
                        User.tenant_id == target_agent.tenant_id,
                        User.is_active == True,  # noqa: E712
                    )
                )
                ids.update(row[0] for row in result.fetchall())
                return ids

            # custom: admins + explicit permissions
            admin_result = await db.execute(
                select(User.id).where(
                    User.tenant_id == target_agent.tenant_id,
                    User.is_active == True,  # noqa: E712
                    User.role.in_(["platform_admin", "org_admin"]),
                )
            )
            ids.update(row[0] for row in admin_result.fetchall())

        perms = await agent_dao.list_permissions(target_agent.id)
        ids.update(
            p.scope_id for p in perms
            if p.scope_type == "user" and p.scope_id and p.access_level in ("use", "manage")
        )
        return ids

    return ids


def _agent_available(agent: Agent | None) -> tuple[bool, str | None]:
    if not agent:
        return False, "target_not_found"
    if getattr(agent, "deleted_at", None) is not None:
        return False, "agent_deleted"
    if getattr(agent, "status", None) in ("stopped", "error"):
        return False, f"target_status_{agent.status}"
    if is_agent_expired(agent):
        return False, "target_expired"
    return True, None


async def evaluate_agent_relationship_status(
    rel_or_db: Any,
    rel_or_none: Any = None,
    *,
    current_user_id: uuid.UUID | None = None,
) -> dict:
    """Compute the effective status for an Agent -> Agent relationship.

    Supports both ``evaluate_agent_relationship_status(rel)`` and legacy ``(db, rel)``.
    """
    from app.dao.agent_dao import agent_dao

    if rel_or_none is not None:
        db = rel_or_db
        rel = rel_or_none
        source_result = await db.execute(select(Agent).where(Agent.id == rel.agent_id))
        source = source_result.scalar_one_or_none()
        target = rel.__dict__.get("target_agent")
        if target is None:
            target_result = await db.execute(select(Agent).where(Agent.id == rel.target_agent_id))
            target = target_result.scalar_one_or_none()
    else:
        db = None
        rel = rel_or_db
        # arch-guard: allow (cross-tenant rel — must load both sides to compare tenant_id)
        source = await agent_dao.get(rel.agent_id)
        target = rel.__dict__.get("target_agent")
        if target is None:
            target = await agent_dao.get(rel.target_agent_id)

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
        if (
            await user_can_manage_agent_id(db, created_by_user_id, source)
            and await user_can_manage_agent_id(db, created_by_user_id, target)
        ):
            return {"access_allowed": True, "access_status": "active", "access_status_reason": None}
        return {
            "access_allowed": False,
            "access_status": "restricted",
            "access_status_reason": "relationship_creator_no_longer_manages_both_agents",
        }

    target_mode = getattr(target, "access_mode", None) or "company"
    if target_mode == "company":
        return {"access_allowed": True, "access_status": "active", "access_status_reason": None}

    candidate_user_ids = [current_user_id, source.creator_id]
    seen: set[uuid.UUID] = set()
    for uid in candidate_user_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        if await user_can_manage_agent_id(db, uid, source) and await user_can_manage_agent_id(db, uid, target):
            return {"access_allowed": True, "access_status": "active", "access_status_reason": None}

    return {
        "access_allowed": False,
        "access_status": "restricted",
        "access_status_reason": "manager_no_longer_has_access_to_both_agents",
    }


async def evaluate_human_relationship_status(
    rel_or_db: Any,
    rel_or_none: Any = None,
    *,
    source_agent: Agent | None = None,
) -> dict:
    """Compute the effective status for an Agent -> Human relationship.

    Supports both ``evaluate_human_relationship_status(rel)`` and legacy ``(db, rel)``.
    """
    from app.dao.agent_dao import agent_dao
    from app.dao.org_member_dao import org_member_dao

    if rel_or_none is not None:
        db = rel_or_db
        rel = rel_or_none
        if source_agent is None:
            source_result = await db.execute(select(Agent).where(Agent.id == rel.agent_id))
            source_agent = source_result.scalar_one_or_none()
        member = rel.__dict__.get("member")
        if member is None:
            member_result = await db.execute(select(OrgMember).where(OrgMember.id == rel.member_id))
            member = member_result.scalar_one_or_none()
    else:
        db = None
        rel = rel_or_db
        if source_agent is None:
            source_agent = await agent_dao.get(rel.agent_id)  # arch-guard: allow
        member = rel.__dict__.get("member")
        if member is None:
            member = await org_member_dao.get(rel.member_id)

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

    return {"access_allowed": True, "access_status": "active", "access_status_reason": None}



async def check_agent_access(
    a1: Any,
    a2: Any = None,
    a3: Any = None,
    *,
    include_deleted: bool = False,
    db: Any = None,
) -> Tuple[Agent, str]:
    """Check if a user has access to a specific agent.

    Supports signatures:
    - ``check_agent_access(db, user, agent_id)`` (legacy / monkeypatched by tests)
    - ``check_agent_access(user, agent_id)``
    - ``check_agent_access(user, agent_id, db)``

    Returns (agent, access_level) where access_level is 'manage' or 'use'.
    """
    from app.dao.agent_dao import agent_dao

    if isinstance(a1, User):
        user = a1
        target_agent_id = a2
    elif isinstance(a2, User):
        user = a2
        target_agent_id = a3
    else:
        user = a2
        target_agent_id = a3

    if include_deleted:
        agent_obj = await agent_dao.get_including_deleted(target_agent_id)
    else:
        agent_obj = await agent_dao.get_active(target_agent_id)

    if not agent_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Tenant isolation check
    if agent_obj.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this agent")

    if agent_obj.creator_id == user.id:
        return agent_obj, "manage"

    if await can_manage_agent(user, agent_obj, include_deleted=include_deleted):
        return agent_obj, "manage"
    if await can_use_agent(user, agent_obj):
        return agent_obj, "use"

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this agent")




def is_agent_creator(user: User, agent: Agent) -> bool:
    """Check if the user is the creator (admin) of the agent."""
    return agent.creator_id == user.id


def is_agent_expired(agent: Agent) -> bool:
    """Return True if the agent is manually marked expired or its expires_at is in the past."""
    if getattr(agent, "is_expired", False):
        return True
    expires_at = getattr(agent, "expires_at", None)
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
