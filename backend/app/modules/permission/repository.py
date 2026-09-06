"""Private Agent visibility persistence operations."""

from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.permission.models import AgentVisibilityGrantRecord, AgentVisibilityRecord


class PermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_visibility(self, visibility: AgentVisibilityRecord) -> None:
        self._session.add(visibility)

    def add_grant(self, grant: AgentVisibilityGrantRecord) -> None:
        self._session.add(grant)

    async def flush(self) -> None:
        await self._session.flush()

    async def get_visibility(self, tenant_id: UUID, agent_id: UUID) -> AgentVisibilityRecord | None:
        statement = select(AgentVisibilityRecord).where(
            AgentVisibilityRecord.tenant_id == tenant_id,
            AgentVisibilityRecord.agent_id == agent_id,
        )
        return await self._one_visibility(statement)

    async def get_membership_grant(
        self, tenant_id: UUID, agent_id: UUID, membership_id: UUID
    ) -> AgentVisibilityGrantRecord | None:
        statement = select(AgentVisibilityGrantRecord).where(
            AgentVisibilityGrantRecord.tenant_id == tenant_id,
            AgentVisibilityGrantRecord.agent_id == agent_id,
            AgentVisibilityGrantRecord.membership_id == membership_id,
        )
        return await self._one_grant(statement)

    async def get_agent_grant(
        self, tenant_id: UUID, agent_id: UUID, source_agent_id: UUID
    ) -> AgentVisibilityGrantRecord | None:
        statement = select(AgentVisibilityGrantRecord).where(
            AgentVisibilityGrantRecord.tenant_id == tenant_id,
            AgentVisibilityGrantRecord.agent_id == agent_id,
            AgentVisibilityGrantRecord.source_agent_id == source_agent_id,
        )
        return await self._one_grant(statement)

    async def list_member_candidate_ids(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[UUID, ...]:
        granted = select(AgentVisibilityGrantRecord.agent_id).where(
            AgentVisibilityGrantRecord.tenant_id == tenant_id,
            AgentVisibilityGrantRecord.membership_id == membership_id,
            AgentVisibilityGrantRecord.revoked_at.is_(None),
        )
        statement = (
            select(AgentVisibilityRecord.agent_id)
            .where(
                AgentVisibilityRecord.tenant_id == tenant_id,
                or_(
                    AgentVisibilityRecord.visibility == "tenant",
                    AgentVisibilityRecord.agent_id.in_(granted),
                ),
            )
            .order_by(AgentVisibilityRecord.agent_id)
            .limit(limit)
            .offset(offset)
        )
        return tuple((await self._session.scalars(statement)).all())

    async def _one_visibility(self, statement: Select[tuple[AgentVisibilityRecord]]) -> AgentVisibilityRecord | None:
        return (await self._session.scalars(statement)).one_or_none()

    async def _one_grant(
        self, statement: Select[tuple[AgentVisibilityGrantRecord]]
    ) -> AgentVisibilityGrantRecord | None:
        return (await self._session.scalars(statement)).one_or_none()
