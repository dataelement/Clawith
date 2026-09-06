"""Private Model configuration persistence operations."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.model.models import ModelRecord, TenantModelDefaultRecord


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_model(self, model: ModelRecord) -> None:
        self._session.add(model)

    def add_default(self, default: TenantModelDefaultRecord) -> None:
        self._session.add(default)

    async def flush(self) -> None:
        await self._session.flush()

    async def get_model(self, tenant_id: UUID, model_id: UUID) -> ModelRecord | None:
        statement = select(ModelRecord).where(
            ModelRecord.tenant_id == tenant_id,
            ModelRecord.id == model_id,
        )
        return await self._one_or_none(statement)

    async def list_models(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[ModelRecord, ...]:
        statement = (
            select(ModelRecord)
            .where(ModelRecord.tenant_id == tenant_id)
            .order_by(ModelRecord.created_at, ModelRecord.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple((await self._session.scalars(statement)).all())

    async def get_default(self, tenant_id: UUID) -> TenantModelDefaultRecord | None:
        statement = select(TenantModelDefaultRecord).where(TenantModelDefaultRecord.tenant_id == tenant_id)
        return (await self._session.scalars(statement)).one_or_none()

    async def _one_or_none(self, statement: Select[tuple[ModelRecord]]) -> ModelRecord | None:
        return (await self._session.scalars(statement)).one_or_none()
