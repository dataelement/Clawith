"""Bounded, Tenant-scoped Tool persistence. Transactions belong to the caller."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tool.models import (
    AgentMCPConnectionRecord,
    AgentToolGrantRecord,
    MembershipAgentToolConnectionRecord,
    ToolDefinitionRecord,
)


class ToolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def definition(self, tenant_id: UUID, definition_id: UUID) -> ToolDefinitionRecord | None:
        return await self.session.scalar(
            select(ToolDefinitionRecord).where(
                ToolDefinitionRecord.tenant_id == tenant_id, ToolDefinitionRecord.id == definition_id
            )
        )

    async def definition_named(self, tenant_id: UUID, name: str) -> ToolDefinitionRecord | None:
        return await self.session.scalar(
            select(ToolDefinitionRecord).where(
                ToolDefinitionRecord.tenant_id == tenant_id, ToolDefinitionRecord.name == name
            )
        )

    async def connection(self, tenant_id: UUID, connection_id: UUID) -> AgentMCPConnectionRecord | None:
        return await self.session.scalar(
            select(AgentMCPConnectionRecord).where(
                AgentMCPConnectionRecord.tenant_id == tenant_id, AgentMCPConnectionRecord.id == connection_id
            )
        )

    async def connection_for_source(
        self, tenant_id: UUID, agent_id: UUID, catalog_id: UUID
    ) -> AgentMCPConnectionRecord | None:
        return await self.session.scalar(
            select(AgentMCPConnectionRecord).where(
                AgentMCPConnectionRecord.tenant_id == tenant_id,
                AgentMCPConnectionRecord.agent_id == agent_id,
                AgentMCPConnectionRecord.catalog_item_id == catalog_id,
            )
        )

    async def grant_for_tool(self, tenant_id: UUID, agent_id: UUID, definition_id: UUID) -> AgentToolGrantRecord | None:
        return await self.session.scalar(
            select(AgentToolGrantRecord).where(
                AgentToolGrantRecord.tenant_id == tenant_id,
                AgentToolGrantRecord.agent_id == agent_id,
                AgentToolGrantRecord.tool_definition_id == definition_id,
            )
        )

    async def personal_connections(
        self, tenant_id: UUID, ids: tuple[UUID, ...]
    ) -> dict[UUID, MembershipAgentToolConnectionRecord]:
        if not ids:
            return {}
        rows = await self.session.scalars(
            select(MembershipAgentToolConnectionRecord).where(
                MembershipAgentToolConnectionRecord.tenant_id == tenant_id,
                MembershipAgentToolConnectionRecord.id.in_(ids),
            )
        )
        return {row.id: row for row in rows}

    async def grants(self, tenant_id: UUID, agent_id: UUID, *, maximum: int) -> list[AgentToolGrantRecord]:
        result = await self.session.scalars(
            select(AgentToolGrantRecord)
            .where(
                AgentToolGrantRecord.tenant_id == tenant_id,
                AgentToolGrantRecord.agent_id == agent_id,
                AgentToolGrantRecord.revoked_at.is_(None),
            )
            .order_by(AgentToolGrantRecord.id)
            .limit(maximum + 1)
        )
        return list(result)

    async def definitions(self, tenant_id: UUID, ids: tuple[UUID, ...]) -> dict[UUID, ToolDefinitionRecord]:
        if not ids:
            return {}
        result = await self.session.scalars(
            select(ToolDefinitionRecord).where(
                ToolDefinitionRecord.tenant_id == tenant_id, ToolDefinitionRecord.id.in_(ids)
            )
        )
        return {row.id: row for row in result}

    async def connections(self, tenant_id: UUID, ids: tuple[UUID, ...]) -> dict[UUID, AgentMCPConnectionRecord]:
        if not ids:
            return {}
        result = await self.session.scalars(
            select(AgentMCPConnectionRecord).where(
                AgentMCPConnectionRecord.tenant_id == tenant_id, AgentMCPConnectionRecord.id.in_(ids)
            )
        )
        return {row.id: row for row in result}

    def add(
        self,
        row: ToolDefinitionRecord
        | AgentMCPConnectionRecord
        | AgentToolGrantRecord
        | MembershipAgentToolConnectionRecord,
    ) -> None:
        self.session.add(row)

    async def flush(self) -> None:
        await self.session.flush()
