"""Organization structure sync service (provider-based only)."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from app.models.identity import IdentityProvider


class OrgSyncService:
    """Sync org structure from a specific identity provider."""

    async def sync_provider(self, db: AsyncSession, provider_id: str) -> dict:
        import uuid as _uuid

        pid = _uuid.UUID(provider_id) if isinstance(provider_id, str) else provider_id

        result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == pid))
        provider = result.scalar_one_or_none()
        if not provider:
            return {"error": f"Identity provider {provider_id} not found"}

        from app.services.org_sync_adapter import get_org_sync_adapter
        adapter = await get_org_sync_adapter(db, provider.provider_type, provider_id=pid)
        if not adapter:
            return {"error": f"Provider type '{provider.provider_type}' not supported for org sync"}

        # Configure adapter
        adapter.provider = provider
        adapter.provider_id = provider.id
        adapter.config = provider.config

        if not provider.tenant_id:
            return {"error": "Identity provider must be bound to a tenant"}

        adapter.tenant_id = provider.tenant_id

        try:
            sync_result = await adapter.sync_org_structure(db)
            await db.commit()
            return sync_result
        except Exception as e:
            logger.error(f"[OrgSync] Provider sync failed: {e}")
            return {"error": str(e)}


org_sync_service = OrgSyncService()
