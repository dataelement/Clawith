"""Bounded, Tenant-scoped Tool persistence. Transactions belong to the caller."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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

    async def insert_definition_if_absent(self, row: ToolDefinitionRecord) -> ToolDefinitionRecord:
        await self.session.execute(insert(ToolDefinitionRecord).values(
            id=row.id, tenant_id=row.tenant_id, created_at=row.created_at, updated_at=row.updated_at,
            catalog_item_id=row.catalog_item_id, source=row.source, name=row.name, upstream_name=row.upstream_name,
            description=row.description, input_schema=row.input_schema, schema_version=row.schema_version,
            executor_key=row.executor_key, configuration_version=row.configuration_version,
            non_secret_config=row.non_secret_config, enabled=row.enabled,
        ).on_conflict_do_nothing(index_elements=["tenant_id", "name"]))
        current = await self.definition_named(row.tenant_id, row.name)
        if current is None:
            raise RuntimeError("Registered Tool definition disappeared")
        return current

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

    async def insert_grant_if_absent(self, row: AgentToolGrantRecord) -> AgentToolGrantRecord:
        await self.session.execute(insert(AgentToolGrantRecord).values(
            id=row.id, tenant_id=row.tenant_id, created_at=row.created_at, updated_at=row.updated_at,
            agent_id=row.agent_id, tool_definition_id=row.tool_definition_id, tool_source=row.tool_source,
            catalog_item_id=row.catalog_item_id, mcp_connection_id=row.mcp_connection_id,
            credential_id=row.credential_id, credential_owner_kind=row.credential_owner_kind,
            credential_owner_id=row.credential_owner_id, configuration_version=row.configuration_version,
            non_secret_config=row.non_secret_config, granted_by_membership_id=row.granted_by_membership_id,
            revoked_at=row.revoked_at,
        ).on_conflict_do_nothing(index_elements=["tenant_id", "agent_id", "tool_definition_id"]))
        current = await self.grant_for_tool(row.tenant_id, row.agent_id, row.tool_definition_id)
        if current is None:
            raise RuntimeError("Registered Tool grant disappeared")
        return current

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

    async def personal_connection_owners(self, tenant_id: UUID, ids: tuple[UUID, ...]) -> dict[UUID, UUID]:
        if not ids:
            return {}
        rows = await self.session.execute(select(MembershipAgentToolConnectionRecord.id,
            MembershipAgentToolConnectionRecord.membership_id).where(
            MembershipAgentToolConnectionRecord.tenant_id == tenant_id,
            MembershipAgentToolConnectionRecord.id.in_(ids)))
        return {identity: membership for identity, membership in rows}

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
