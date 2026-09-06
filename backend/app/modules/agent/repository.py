"""Private Agent persistence operations."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.models import AgentRecord


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, agent: AgentRecord) -> None:
        self._session.add(agent)

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, tenant_id: UUID, agent_id: UUID) -> AgentRecord | None:
        statement = select(AgentRecord).where(
            AgentRecord.tenant_id == tenant_id,
            AgentRecord.id == agent_id,
        )
        return await self._one_or_none(statement)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        active_only: bool = False,
    ) -> tuple[AgentRecord, ...]:
        statement = select(AgentRecord).where(AgentRecord.tenant_id == tenant_id)
        if active_only:
            statement = statement.where(AgentRecord.enabled.is_(True), AgentRecord.archived_at.is_(None))
        statement = statement.order_by(AgentRecord.created_at, AgentRecord.id).limit(limit).offset(offset)
        return tuple((await self._session.scalars(statement)).all())

    async def list_by_ids(
        self, tenant_id: UUID, agent_ids: tuple[UUID, ...], *, active_only: bool
    ) -> tuple[AgentRecord, ...]:
        if not agent_ids:
            return ()
        statement = select(AgentRecord).where(
            AgentRecord.tenant_id == tenant_id,
            AgentRecord.id.in_(agent_ids),
        )
        if active_only:
            statement = statement.where(AgentRecord.enabled.is_(True), AgentRecord.archived_at.is_(None))
        statement = statement.order_by(AgentRecord.id)
        return tuple((await self._session.scalars(statement)).all())

    async def _one_or_none(self, statement: Select[tuple[AgentRecord]]) -> AgentRecord | None:
        return (await self._session.scalars(statement)).one_or_none()
