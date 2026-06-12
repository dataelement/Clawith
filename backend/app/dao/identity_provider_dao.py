"""DAO for IdentityProvider model."""

from typing import Any

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.identity import IdentityProvider


class IdentityProviderDAO(BaseDAO[IdentityProvider]):
    """DAO for IdentityProvider model."""

    def __init__(self) -> None:
        super().__init__(IdentityProvider)

    async def get_by_type_and_tenant(
        self,
        provider_type: str,
        tenant_id: Any | None,
    ) -> IdentityProvider | None:
        """Find an IdentityProvider by type scoped to a tenant (or global if None)."""
        async with self.session() as db:
            query = select(IdentityProvider).where(
                IdentityProvider.provider_type == provider_type,
            )
            if tenant_id is None:
                query = query.where(IdentityProvider.tenant_id.is_(None))
            else:
                query = query.where(IdentityProvider.tenant_id == tenant_id)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_or_create(
        self,
        provider_type: str,
        tenant_id: Any | None,
        *,
        name: str | None = None,
        sso_login_enabled: bool = False,
    ) -> IdentityProvider:
        """Get an existing IdentityProvider or create it if missing."""
        provider = await self.get_by_type_and_tenant(provider_type, tenant_id)
        if provider:
            return provider

        async with self.session() as db:
            provider = IdentityProvider(
                provider_type=provider_type,
                name=name or provider_type.capitalize(),
                is_active=True,
                sso_login_enabled=sso_login_enabled,
                config={},
                tenant_id=tenant_id,
            )
            db.add(provider)
            await db.flush()
            return provider


identity_provider_dao = IdentityProviderDAO()
