"""Enterprise information synchronization service.

Uses Redis Pub/Sub to notify online Agent containers when enterprise info changes.
Agents pull latest data based on their roles and write to local enterprise_info/ directory.
"""

import json
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import query_dao
from app.core.events import publish_event
from app.models.agent import Agent
from app.models.audit import EnterpriseInfo
from app.services.storage import store_agent_bytes

# Redis channel for enterprise info updates
ENTERPRISE_INFO_CHANNEL = "enterprise_info_updated"


class EnterpriseSyncService:
    """Synchronize enterprise information to online Agent containers within tenant scope."""

    async def update_enterprise_info(
        self, db: AsyncSession, tenant_id: uuid.UUID, info_type: str, content: dict,
        visible_roles: list[str], updated_by: uuid.UUID
    ) -> EnterpriseInfo:
        """Update enterprise info in database for a specific tenant and notify tenant agents."""
        result = await query_dao.execute(db, 
            select(EnterpriseInfo).where(
                EnterpriseInfo.tenant_id == tenant_id,
                EnterpriseInfo.info_type == info_type,
            )
        )
        info = result.scalar_one_or_none()

        if info:
            info.content = content
            info.visible_roles = visible_roles
            info.version += 1
            info.updated_by = updated_by
        else:
            info = EnterpriseInfo(
                tenant_id=tenant_id,
                info_type=info_type,
                content=content,
                visible_roles=visible_roles,
                updated_by=updated_by,
            )
            query_dao.add(db, info)

        await query_dao.flush(db)

        # Publish update event with tenant_id scope
        await publish_event(ENTERPRISE_INFO_CHANNEL, {
            "tenant_id": str(tenant_id),
            "info_type": info_type,
            "version": info.version,
            "visible_roles": visible_roles,
        })

        logger.info(f"Published enterprise_info update for tenant {tenant_id}: {info_type} v{info.version}")
        return info

    async def sync_to_agent(self, db: AsyncSession, agent_id: uuid.UUID, agent_role: str = "") -> None:
        """Pull enterprise info from DB and write to agent's enterprise_info/ directory.

        Strictly filters EnterpriseInfo entries by the agent's tenant_id and role.
        """
        agent_result = await query_dao.execute(db, select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent or not agent.tenant_id:
            logger.warning(f"Skipping enterprise_info sync for invalid agent {agent_id}")
            return

        result = await query_dao.execute(
            db, select(EnterpriseInfo).where(EnterpriseInfo.tenant_id == agent.tenant_id)
        )
        all_info = result.scalars().all()

        for info in all_info:
            # Filter by role visibility
            if info.visible_roles and agent_role and agent_role not in info.visible_roles:
                continue

            await store_agent_bytes(
                agent_id,
                f"enterprise_info/{info.info_type}.json",
                json.dumps({
                    "type": info.info_type,
                    "version": info.version,
                    "content": info.content,
                }, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )

        logger.info(f"Synced tenant {agent.tenant_id} enterprise info to agent {agent_id}")

    async def sync_to_all_agents(self, db: AsyncSession, tenant_id: uuid.UUID) -> int:
        """Sync enterprise info to running agents strictly belonging to the given tenant. Returns count."""
        result = await query_dao.execute(
            db,
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.status == "running",
                Agent.deleted_at.is_(None),
            )
        )
        agents = result.scalars().all()

        for agent in agents:
            await self.sync_to_agent(db, agent.id, agent.role_description)

        logger.info(f"Synced enterprise info to {len(agents)} agents in tenant {tenant_id}")
        return len(agents)


enterprise_sync_service = EnterpriseSyncService()
