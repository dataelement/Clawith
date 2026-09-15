"""S0 Identity/Tenant schema integration against disposable PostgreSQL."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Base
from app.modules.identity_tenant.models import AccountRecord, MembershipRecord, TenantRecord


def _now() -> datetime:
    return datetime.now(UTC)


def test_s0_registers_the_exact_owned_tables() -> None:
    assert {
        name: Base.metadata.tables[name].info["owner"]
        for name in ("accounts", "tenants", "memberships")
    } == {
        "accounts": "identity_tenant",
        "tenants": "identity_tenant",
        "memberships": "identity_tenant",
    }


@pytest.mark.asyncio
async def test_s0_accepts_explicit_account_tenant_and_membership(db_session: AsyncSession) -> None:
    now = _now()
    account = AccountRecord(enabled=True, platform_role=None, created_at=now, updated_at=now)
    tenant = TenantRecord(name="Example Tenant", enabled=True, created_at=now, updated_at=now)
    db_session.add_all([account, tenant])
    await db_session.flush()
    membership = MembershipRecord(
        tenant_id=tenant.id,
        account_id=account.id,
        display_name="Ada",
        avatar=None,
        title=None,
        role="tenant_admin",
        enabled=True,
        joined_at=now,
        updated_at=now,
    )
    db_session.add(membership)
    await db_session.flush()

    assert membership.id is not None


@pytest.mark.asyncio
async def test_s0_rejects_duplicate_tenant_membership(db_session: AsyncSession) -> None:
    now = _now()
    account = AccountRecord(enabled=True, platform_role=None, created_at=now, updated_at=now)
    tenant = TenantRecord(name="Example Tenant", enabled=True, created_at=now, updated_at=now)
    db_session.add_all([account, tenant])
    await db_session.flush()
    values = {
        "tenant_id": tenant.id,
        "account_id": account.id,
        "display_name": "Ada",
        "avatar": None,
        "title": None,
        "role": "member",
        "enabled": True,
        "joined_at": now,
        "updated_at": now,
    }
    db_session.add_all([MembershipRecord(**values), MembershipRecord(**values)])

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s0_rejects_unknown_closed_roles(db_session: AsyncSession) -> None:
    now = _now()
    account = AccountRecord(enabled=True, platform_role=None, created_at=now, updated_at=now)
    tenant = TenantRecord(name="Example Tenant", enabled=True, created_at=now, updated_at=now)
    db_session.add_all([account, tenant])
    await db_session.flush()
    db_session.add(
        MembershipRecord(
            tenant_id=tenant.id,
            account_id=account.id,
            display_name="Ada",
            avatar=None,
            title=None,
            role="owner",
            enabled=True,
            joined_at=now,
            updated_at=now,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
