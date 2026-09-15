"""Private Credential persistence operations."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.credential.models import CredentialRecord


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: CredentialRecord) -> None:
        self._session.add(record)

    async def flush(self) -> None:
        await self._session.flush()

    async def refresh(self, record: CredentialRecord) -> None:
        await self._session.refresh(record)

    async def get(self, tenant_id: UUID, credential_id: UUID) -> CredentialRecord | None:
        statement = select(CredentialRecord).where(
            CredentialRecord.tenant_id == tenant_id,
            CredentialRecord.id == credential_id,
        )
        return await self._one_or_none(statement)

    async def list_accessible(
        self,
        tenant_id: UUID,
        *,
        membership_id: UUID,
        manage_all: bool,
        limit: int,
        offset: int,
    ) -> tuple[CredentialRecord, ...]:
        statement = (
            select(CredentialRecord)
            .where(CredentialRecord.tenant_id == tenant_id)
            .order_by(CredentialRecord.created_at, CredentialRecord.id)
            .limit(limit)
            .offset(offset)
        )
        if not manage_all:
            statement = statement.where(CredentialRecord.membership_owner_id == membership_id)
        return tuple((await self._session.scalars(statement)).all())

    async def _one_or_none(self, statement: Select[tuple[CredentialRecord]]) -> CredentialRecord | None:
        return (await self._session.scalars(statement)).one_or_none()
