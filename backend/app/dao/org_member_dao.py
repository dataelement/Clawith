"""DAO for OrgMember model."""

from typing import Any, Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.org import OrgMember


class OrgMemberDAO(BaseDAO[OrgMember]):
    """DAO for OrgMember model."""

    def __init__(self) -> None:
        super().__init__(OrgMember)

    async def find_unbound_by_email(
        self,
        email: str,
        tenant_id: Any,
    ) -> OrgMember | None:
        """Find an OrgMember without a linked user that matches by email."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.email == email,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def find_unbound_by_phone(
        self,
        phone: str,
        tenant_id: Any,
    ) -> OrgMember | None:
        """Find an OrgMember without a linked user that matches by phone."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.phone == phone,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def get_by_user_and_provider(
        self,
        user_id: Any,
        tenant_id: Any,
        provider_id: Any,
    ) -> OrgMember | None:
        """Find the OrgMember record for a user under a specific provider."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user_id,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.provider_id == provider_id,
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def find_unbound_by_email_and_provider(
        self,
        email: str,
        tenant_id: Any,
        provider_id: Any,
    ) -> OrgMember | None:
        """Find an unlinked OrgMember by email under a specific provider."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.email == email,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.provider_id == provider_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def find_unbound_by_phone_and_provider(
        self,
        phone: str,
        tenant_id: Any,
        provider_id: Any,
    ) -> OrgMember | None:
        """Find an unlinked OrgMember by phone under a specific provider."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.phone == phone,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.provider_id == provider_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def get_by_user_and_tenant_and_provider(
        self,
        user_id: Any,
        tenant_id: Any,
        provider_id: Any,
    ) -> Sequence[OrgMember]:
        """Get all OrgMember records for a user+tenant+provider combination."""
        async with self.session() as db:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user_id,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.provider_id == provider_id,
                )
            )
            return result.scalars().all()


org_member_dao = OrgMemberDAO()
