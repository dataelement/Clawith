"""Hook to automatically bind new users and non-private agents to the OKR Agent."""

import uuid
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember

async def hook_new_org_member(db: AsyncSession, member_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """When a new OrgMember is created or bound, bind them to the system OKR Agent if it exists."""
    okr_agent = await _get_okr_agent(db, tenant_id)
    if not okr_agent:
        return

    # Check if relationship already exists
    existing = await db.execute(
        select(AgentRelationship).where(
            AgentRelationship.agent_id == okr_agent.id,
            AgentRelationship.member_id == member_id
        )
    )
    if not existing.scalar_one_or_none():
        db.add(AgentRelationship(
            agent_id=okr_agent.id,
            member_id=member_id,
            relation="okr_coordinator"
        ))
        logger.info(f"[OKR Hook] Auto-bound OrgMember {member_id} to OKR Agent {okr_agent.id}")

async def hook_new_agent(db: AsyncSession, new_agent_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """When a new non-private agent is created, bind to OKR Agent."""
    from sqlalchemy.orm import selectinload
    
    # Check if new agent is private
    agent_res = await db.execute(
        select(Agent)
        .options(selectinload(Agent.permissions))
        .where(Agent.id == new_agent_id)
    )
    agent = agent_res.scalar_one_or_none()
    if not agent or getattr(agent, "is_system", False):
        return
        
    permissions = agent.permissions
    # Private = user scoped
    if any(p.scope_type == "user" for p in permissions):
        return  # Do not bind private agents
        
    okr_agent = await _get_okr_agent(db, tenant_id)
    if not okr_agent:
        return
        
    # Bind OKR Agent -> New Agent
    existing1 = await db.execute(
        select(AgentAgentRelationship).where(
            AgentAgentRelationship.agent_id == okr_agent.id,
            AgentAgentRelationship.target_agent_id == new_agent_id
        )
    )
    if not existing1.scalar_one_or_none():
        db.add(AgentAgentRelationship(
            agent_id=okr_agent.id,
            target_agent_id=new_agent_id,
            relation="okr_coordinator"
        ))
        
    # Bind New Agent -> OKR Agent (Mutual)
    existing2 = await db.execute(
        select(AgentAgentRelationship).where(
            AgentAgentRelationship.agent_id == new_agent_id,
            AgentAgentRelationship.target_agent_id == okr_agent.id
        )
    )
    if not existing2.scalar_one_or_none():
        db.add(AgentAgentRelationship(
            agent_id=new_agent_id,
            target_agent_id=okr_agent.id,
            relation="okr_coordinator"
        ))

    logger.info(f"[OKR Hook] Auto-bound Agent {new_agent_id} to OKR Agent {okr_agent.id}")

async def _get_okr_agent(db: AsyncSession, tenant_id: uuid.UUID) -> Agent | None:
    # Find system agent named 'OKR Agent' in this tenant
    res = await db.execute(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.is_system == True,
            Agent.name == "OKR Agent"
        ).limit(1)
    )
    return res.scalar_one_or_none()
