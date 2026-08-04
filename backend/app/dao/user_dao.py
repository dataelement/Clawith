from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dao.base import BaseDAO
from app.models.user import Identity, User
from app.models.tenant import Tenant


class UserDAO(BaseDAO[User]):
    """DAO for User model handling tenant-scoped user records."""

    def __init__(self) -> None:
        super().__init__(User)

    async def get_by_identity_and_tenant(self, identity_id: Any, tenant_id: Any | None) -> User | None:
        """Find a user in a specific tenant (or tenant-less) by identity ID."""
        async with self.session(readonly=True) as db:
            query = select(User).where(User.identity_id == identity_id)
            if tenant_id is not None:
                query = query.where(User.tenant_id == tenant_id)
            else:
                query = query.where(User.tenant_id.is_(None))
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_identity_id(self, identity_id: Any, include_identity: bool = False) -> Sequence[User]:
        """Find all users associated with an identity ID."""
        async with self.session(readonly=True) as db:
            query = select(User).where(User.identity_id == identity_id)
            if include_identity:
                query = query.options(selectinload(User.identity))
            result = await db.execute(query)
            return result.scalars().all()

    async def get_login_users_with_tenants(self, identity_id: Any) -> Sequence[tuple[User, Tenant | None]]:
        """Fetch login candidate users with tenant metadata in one round trip."""
        async with self.session(readonly=True) as db:
            query = (
                select(User, Tenant)
                .outerjoin(Tenant, User.tenant_id == Tenant.id)
                .where(User.identity_id == identity_id)
                .options(selectinload(User.identity))
            )
            result = await db.execute(query)
            return result.all()

    async def get_by_identity_username(self, username: str) -> User | None:
        """Find user by identity username."""
        async with self.session(readonly=True) as db:
            query = select(User).join(Identity, User.identity_id == Identity.id).where(Identity.username == username)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_email_and_tenant(
        self, email: str, tenant_id: Any | None, exclude_user_id: Any | None = None
    ) -> User | None:
        """Find user by identity email in a specific tenant, optionally excluding a user ID."""
        async with self.session(readonly=True) as db:
            query = (
                select(User)
                .join(Identity, User.identity_id == Identity.id)
                .where(
                    Identity.email == email,
                    User.tenant_id == tenant_id,
                )
            )
            if exclude_user_id is not None:
                query = query.where(User.id != exclude_user_id)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_phone_and_tenant(
        self, phone: str, tenant_id: Any | None, exclude_user_id: Any | None = None
    ) -> User | None:
        """Find user by identity phone in a specific tenant, optionally excluding a user ID."""
        async with self.session(readonly=True) as db:
            query = (
                select(User)
                .join(Identity, User.identity_id == Identity.id)
                .where(
                    Identity.phone == phone,
                    User.tenant_id == tenant_id,
                )
            )
            if exclude_user_id is not None:
                query = query.where(User.id != exclude_user_id)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_with_identity(self, user_id: Any) -> User | None:
        """Fetch user by ID with identity preloaded."""
        async with self.session(readonly=True) as db:
            query = select(User).where(User.id == user_id).options(selectinload(User.identity))
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_representative_user_for_identity(self, identity_id: Any) -> User | None:
        """Find a representative user (e.g. latest created) associated with an identity ID."""
        async with self.session(readonly=True) as db:
            query = select(User).where(User.identity_id == identity_id).order_by(User.created_at.desc()).limit(1)
            result = await db.execute(query)
            return result.scalar_one_or_none()


    async def list_admin_users(self, tenant_id: Any) -> Sequence[User]:
        """Fetch all active org/platform admin users in a tenant."""
        if not tenant_id:
            return []
        async with self.session(readonly=True) as db:
            query = select(User).where(
                User.tenant_id == tenant_id,
                User.is_active == True,  # noqa: E712
                User.role.in_(["platform_admin", "org_admin"]),
            )
            return (await db.execute(query)).scalars().all()

    async def list_by_ids(self, user_ids: Sequence[Any], db: Any = None) -> Sequence[User]:
        """Fetch users by a list of user IDs."""
        if not user_ids:
            return []
        async with self.session(db=db, readonly=True) as session_db:
            query = select(User).where(User.id.in_(user_ids))
            return (await session_db.execute(query)).scalars().all()


user_dao = UserDAO()

