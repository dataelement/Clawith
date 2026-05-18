"""Helpers for resolving identity providers safely."""

from __future__ import annotations

from typing import Iterable

from loguru import logger
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import IdentityProvider


def build_identity_provider_query(
    provider_type: str,
    tenant_id: str | None = None,
    *,
    is_active: bool | None = None,
) -> Select[tuple[IdentityProvider]]:
    """Build a deterministic provider lookup query."""
    query = select(IdentityProvider).where(IdentityProvider.provider_type == provider_type)
    if tenant_id is not None:
        query = query.where(IdentityProvider.tenant_id == tenant_id)
    else:
        query = query.where(IdentityProvider.tenant_id.is_(None))
    if is_active is not None:
        query = query.where(IdentityProvider.is_active == is_active)
    return query.order_by(
        IdentityProvider.updated_at.desc(),
        IdentityProvider.created_at.desc(),
        IdentityProvider.id.desc(),
    )


def choose_preferred_identity_provider(
    providers: Iterable[IdentityProvider],
    *,
    provider_type: str,
    tenant_id: str | None = None,
) -> IdentityProvider | None:
    """Pick the preferred provider and warn when duplicates are present."""
    items = list(providers)
    if not items:
        return None

    if len(items) > 1:
        logger.warning(
            "Multiple identity providers found for type=%s tenant_id=%s; using provider_id=%s",
            provider_type,
            tenant_id,
            items[0].id,
        )
    return items[0]


async def get_preferred_identity_provider(
    db: AsyncSession,
    provider_type: str,
    tenant_id: str | None = None,
    *,
    is_active: bool | None = None,
) -> IdentityProvider | None:
    """Fetch the preferred provider without raising on duplicate rows."""
    result = await db.execute(
        build_identity_provider_query(provider_type, tenant_id, is_active=is_active)
    )
    return choose_preferred_identity_provider(
        result.scalars().all(),
        provider_type=provider_type,
        tenant_id=tenant_id,
    )
