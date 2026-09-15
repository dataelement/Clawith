"""Private append-only Audit persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditRecord


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: AuditRecord) -> None:
        self._session.add(record)

    async def flush(self) -> None:
        await self._session.flush()

    async def list(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[AuditRecord, ...]:
        statement = (
            select(AuditRecord)
            .where(AuditRecord.tenant_id == tenant_id)
            .order_by(AuditRecord.occurred_at.desc(), AuditRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return tuple((await self._session.scalars(statement)).all())
