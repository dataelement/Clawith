"""Tenant-scoped Catalog persistence; installations remain with their owners."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.capability_market.models import CapabilityCatalogItemRecord


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enabled_sources(
        self, tenant_id: UUID, item_ids: frozenset[UUID]
    ) -> tuple[CapabilityCatalogItemRecord, ...]:
        rows = await self.session.scalars(
            select(CapabilityCatalogItemRecord)
            .where(
                CapabilityCatalogItemRecord.tenant_id == tenant_id,
                CapabilityCatalogItemRecord.id.in_(item_ids),
                CapabilityCatalogItemRecord.enabled.is_(True),
            )
            .limit(len(item_ids))
        )
        return tuple(rows)

    async def get(self, tenant_id: UUID, item_id: UUID, *, lock: bool = False) -> CapabilityCatalogItemRecord | None:
        statement = select(CapabilityCatalogItemRecord).where(
            CapabilityCatalogItemRecord.id == item_id,
            or_(CapabilityCatalogItemRecord.tenant_id == tenant_id, CapabilityCatalogItemRecord.tenant_id.is_(None)),
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def register(self, row: CapabilityCatalogItemRecord) -> tuple[CapabilityCatalogItemRecord, bool]:
        values = {column.key: getattr(row, column.key) for column in row.__table__.columns if column.computed is None}
        identity = ["kind", "source", "source_key"]
        predicate = CapabilityCatalogItemRecord.tenant_id.is_(None)
        if row.tenant_id is not None:
            identity.insert(0, "tenant_id")
            predicate = CapabilityCatalogItemRecord.tenant_id.is_not(None)
        statement = (
            insert(CapabilityCatalogItemRecord)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=identity,
                index_where=predicate,
            )
            .returning(CapabilityCatalogItemRecord)
        )
        created = await self.session.scalar(statement)
        if created is not None:
            return created, True
        existing = await self.session.scalar(
            select(CapabilityCatalogItemRecord).where(
                CapabilityCatalogItemRecord.tenant_id == row.tenant_id,
                CapabilityCatalogItemRecord.kind == row.kind,
                CapabilityCatalogItemRecord.source == row.source,
                CapabilityCatalogItemRecord.source_key == row.source_key,
            )
        )
        assert existing is not None
        return existing, False

    async def search(
        self, tenant_id: UUID, query: str, kind: str | None, limit: int, offset: int
    ) -> tuple[CapabilityCatalogItemRecord, ...]:
        statement = select(CapabilityCatalogItemRecord).where(
            or_(CapabilityCatalogItemRecord.tenant_id == tenant_id, CapabilityCatalogItemRecord.tenant_id.is_(None)),
            CapabilityCatalogItemRecord.enabled.is_(True),
        )
        if query:
            statement = statement.where(CapabilityCatalogItemRecord.name.icontains(query, autoescape=True))
        if kind is not None:
            statement = statement.where(CapabilityCatalogItemRecord.kind == kind)
        rows = await self.session.scalars(
            statement.order_by(
                CapabilityCatalogItemRecord.name,
                CapabilityCatalogItemRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(rows)
