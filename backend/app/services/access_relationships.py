"""Helpers that keep access permissions and relationship prerequisites aligned."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import query_dao
from app.core.permissions import get_agent_accessible_user_ids
from app.dao import agent_access_dao
from app.database import bind_session_context
from app.models.agent import Agent
from app.models.org import AgentRelationship
from app.services.registration_service import registration_service


async def ensure_access_granted_platform_relationships(
    db: AsyncSession,
    agent: Agent,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> bool:
    """Ensure private/custom platform users are in the agent's human network.

    Platform messages intentionally require an active human relationship. For
    private/custom agents, the access list is already the user's explicit
    relationship boundary, so we materialize those platform users as human
    relationships. Company-wide agents stay explicit to avoid adding every
    tenant user to every public agent.

    Returns True when new relationship rows were added.
    """
    access_mode = getattr(agent, "access_mode", None) or "company"
    if access_mode not in ("private", "custom") or not agent.tenant_id:
        return False

    user_ids = await get_agent_accessible_user_ids(db, agent)
    if not user_ids:
        return False

    async with bind_session_context(db):
        existing_user_ids = await agent_access_dao.list_active_relationship_user_ids(
            agent_id=agent.id,
            tenant_id=agent.tenant_id,
            user_ids=user_ids,
        )
    missing_user_ids = user_ids - existing_user_ids
    if not missing_user_ids:
        return False

    async with bind_session_context(db):
        users = await agent_access_dao.list_active_users_by_ids(user_ids=missing_user_ids, tenant_id=agent.tenant_id)

    changed = False
    for user in users:
        member = await registration_service.ensure_web_org_member(user)
        if not member or member.status != "active":
            continue
        query_dao.add(db, 
            AgentRelationship(
                agent_id=agent.id,
                member_id=member.id,
                relation="collaborator",
                description="Auto-added from agent access permissions.",
                created_by_user_id=created_by_user_id or agent.creator_id,
                updated_by_user_id=created_by_user_id or agent.creator_id,
            )
        )
        changed = True

    if changed:
        await query_dao.flush(db)

    return changed
