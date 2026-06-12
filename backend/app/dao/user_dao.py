from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dao.base import BaseDAO
from app.models.user import Identity, User


class UserDAO(BaseDAO[User]):
    """DAO for User model handling tenant-scoped user records."""

    def __init__(self) -> None:
        super().__init__(User)

    async def get_by_identity_and_tenant(self, identity_id: Any, tenant_id: Any | None) -> User | None:
        """Find a user in a specific tenant (or tenant-less) by identity ID."""
        async with self.session() as db:
            query = select(User).where(User.identity_id == identity_id)
            if tenant_id is not None:
                query = query.where(User.tenant_id == tenant_id)
            else:
                query = query.where(User.tenant_id.is_(None))
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_identity_id(self, identity_id: Any, include_identity: bool = False) -> Sequence[User]:
        """Find all users associated with an identity ID."""
        async with self.session() as db:
            query = select(User).where(User.identity_id == identity_id)
            if include_identity:
                query = query.options(selectinload(User.identity))
            result = await db.execute(query)
            return result.scalars().all()

    async def get_by_identity_username(self, username: str) -> User | None:
        """Find user by identity username."""
        async with self.session() as db:
            query = select(User).join(Identity, User.identity_id == Identity.id).where(Identity.username == username)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_email_and_tenant(
        self, email: str, tenant_id: Any | None, exclude_user_id: Any | None = None
    ) -> User | None:
        """Find user by identity email in a specific tenant, optionally excluding a user ID."""
        async with self.session() as db:
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
        async with self.session() as db:
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
        async with self.session() as db:
            query = select(User).where(User.id == user_id).options(selectinload(User.identity))
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_representative_user_for_identity(self, identity_id: Any) -> User | None:
        """Find a representative user (e.g. latest created) associated with an identity ID."""
        async with self.session() as db:
            query = select(User).where(User.identity_id == identity_id).order_by(User.created_at.desc()).limit(1)
            result = await db.execute(query)
            return result.scalar_one_or_none()


user_dao = UserDAO()
