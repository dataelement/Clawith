"""Private Identity and Tenant persistence operations."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity_tenant.models import AccountRecord, MembershipRecord, TenantRecord


class IdentityTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_account(self, account: AccountRecord) -> None:
        self._session.add(account)

    def add_tenant(self, tenant: TenantRecord) -> None:
        self._session.add(tenant)

    def add_membership(self, membership: MembershipRecord) -> None:
        self._session.add(membership)

    async def flush(self) -> None:
        await self._session.flush()

    async def get_account(self, account_id: UUID) -> AccountRecord | None:
        return await self._session.get(AccountRecord, account_id)

    async def get_tenant(self, tenant_id: UUID) -> TenantRecord | None:
        return await self._session.get(TenantRecord, tenant_id)

    async def get_membership(
        self, tenant_id: UUID, membership_id: UUID
    ) -> MembershipRecord | None:
        statement = select(MembershipRecord).where(
            MembershipRecord.tenant_id == tenant_id,
            MembershipRecord.id == membership_id,
        )
        return await self._one_or_none(statement)

    async def get_membership_for_account(
        self, tenant_id: UUID, account_id: UUID
    ) -> MembershipRecord | None:
        statement = select(MembershipRecord).where(
            MembershipRecord.tenant_id == tenant_id,
            MembershipRecord.account_id == account_id,
        )
        return await self._one_or_none(statement)

    async def list_memberships(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[MembershipRecord, ...]:
        statement = (
            select(MembershipRecord)
            .where(MembershipRecord.tenant_id == tenant_id)
            .order_by(MembershipRecord.joined_at, MembershipRecord.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple((await self._session.scalars(statement)).all())

    async def _one_or_none(self, statement: Select[tuple[MembershipRecord]]) -> MembershipRecord | None:
        return (await self._session.scalars(statement)).one_or_none()
