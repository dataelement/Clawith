"""Private Workspace persistence; callers own short transactions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspace.models import AgentSkillBindingRecord, SkillPackageRecord, WorkspaceRecord


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace(self, tenant_id: UUID, kind: str, subject_id: UUID) -> WorkspaceRecord | None:
        column = {
            "membership": WorkspaceRecord.membership_id,
            "agent": WorkspaceRecord.agent_id,
            "group": WorkspaceRecord.group_id,
        }[kind]
        return await self.session.scalar(
            select(WorkspaceRecord).where(WorkspaceRecord.tenant_id == tenant_id, column == subject_id)
        )

    async def binding(self, tenant_id: UUID, agent_id: UUID, name: str) -> AgentSkillBindingRecord | None:
        return await self.session.scalar(
            select(AgentSkillBindingRecord).where(
                AgentSkillBindingRecord.tenant_id == tenant_id,
                AgentSkillBindingRecord.agent_id == agent_id,
                AgentSkillBindingRecord.skill_name == name,
            )
        )

    async def package(self, tenant_id: UUID, package_id: UUID) -> SkillPackageRecord | None:
        return await self.session.scalar(
            select(SkillPackageRecord).where(
                SkillPackageRecord.tenant_id == tenant_id, SkillPackageRecord.id == package_id
            )
        )

    async def discovery_sources(self, tenant_id: UUID, agent_id: UUID, limit: int) -> list[tuple[str, UUID | None]]:
        rows = await self.session.execute(
            select(AgentSkillBindingRecord.skill_name, SkillPackageRecord.catalog_item_id)
            .join(
                SkillPackageRecord,
                (SkillPackageRecord.tenant_id == AgentSkillBindingRecord.tenant_id)
                & (SkillPackageRecord.id == AgentSkillBindingRecord.package_id),
            )
            .where(AgentSkillBindingRecord.tenant_id == tenant_id, AgentSkillBindingRecord.agent_id == agent_id)
            .order_by(AgentSkillBindingRecord.skill_name)
            .limit(limit)
        )
        return [(name, catalog_id) for name, catalog_id in rows]
