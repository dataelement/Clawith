"""Helpers that keep access permissions and relationship prerequisites aligned."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import get_agent_accessible_user_ids
from app.models.agent import Agent
from app.models.org import AgentRelationship, OrgMember
from app.models.user import User
from app.services.registration_service import registration_service


async def ensure_access_granted_platform_relationships(
    db: AsyncSession,
    agent: Agent,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> bool:
    """Ensure private creator access is present in the legacy human network.

    The roster-driven model no longer uses legacy relationship rows to decide
    who can contact whom. This helper only keeps the old Relationships surface
    usable for private agents, where the creator is still the sole human member
    worth materializing. Company and custom agents both have company-wide use
    access, so materializing them would add every tenant user to legacy data.

    Returns True when new relationship rows were added.
    """
    access_mode = getattr(agent, "access_mode", None) or "company"
    if access_mode != "private" or not agent.tenant_id:
        return False

    user_ids = await get_agent_accessible_user_ids(db, agent)
    if not user_ids:
        return False

    existing_result = await db.execute(
        select(OrgMember.user_id)
        .join(AgentRelationship, AgentRelationship.member_id == OrgMember.id)
        .where(
            AgentRelationship.agent_id == agent.id,
            OrgMember.tenant_id == agent.tenant_id,
            OrgMember.status == "active",
            OrgMember.user_id.in_(user_ids),
        )
    )
    existing_user_ids = {row[0] for row in existing_result.fetchall() if row[0]}
    missing_user_ids = user_ids - existing_user_ids
    if not missing_user_ids:
        return False

    users_result = await db.execute(
        select(User).where(
            User.id.in_(missing_user_ids),
            User.tenant_id == agent.tenant_id,
            User.is_active == True,  # noqa: E712
        )
    )

    changed = False
    for user in users_result.scalars().all():
        member = await registration_service.ensure_web_org_member(user)
        if not member or member.status != "active":
            continue
        db.add(
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
        await db.flush()

    return changed
