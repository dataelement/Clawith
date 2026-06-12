from typing import Any, Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.tenant import Tenant


class TenantDAO(BaseDAO[Tenant]):
    """DAO for Tenant model handling organization-scoped records."""

    def __init__(self) -> None:
        super().__init__(Tenant)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Find a tenant by its unique slug identifier."""
        async with self.session() as db:
            query = select(Tenant).where(Tenant.slug == slug)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_ids(self, ids: Sequence[Any]) -> Sequence[Tenant]:
        """Find multiple tenants by a list of their IDs."""
        if not ids:
            return []
        async with self.session() as db:
            query = select(Tenant).where(Tenant.id.in_(ids))
            result = await db.execute(query)
            return result.scalars().all()

    async def get_by_sso_domain(self, domain: str) -> Tenant | None:
        """Find an active tenant matching the given SSO email domain."""
        async with self.session() as db:
            result = await db.execute(
                select(Tenant).where(
                    Tenant.sso_domain == domain.lower(),
                    Tenant.is_active == True,
                )
            )
            return result.scalar_one_or_none()


tenant_dao = TenantDAO()
