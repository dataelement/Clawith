import re
import uuid
from typing import Any

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.user import Identity


class IdentityDAO(BaseDAO[Identity]):
    """DAO for Identity model handling authentication credentials."""

    def __init__(self) -> None:
        super().__init__(Identity)

    async def get_by_login_identifier(self, identifier: str) -> Identity | None:
        """Find identity by email, phone, or username."""
        async with self.session() as db:
            query = select(Identity).where(
                (Identity.email == identifier) | (Identity.phone == identifier) | (Identity.username == identifier)
            )
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Identity | None:
        """Find identity by email address."""
        async with self.session() as db:
            query = select(Identity).where(Identity.email == email)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Identity | None:
        """Find identity by username."""
        async with self.session() as db:
            query = select(Identity).where(Identity.username == username)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Identity | None:
        """Find identity by normalized phone number."""
        normalized = re.sub(r"[\s\-\+]", "", phone)
        async with self.session() as db:
            query = select(Identity).where(Identity.phone == normalized)
            result = await db.execute(query)
            return result.scalar_one_or_none()

    async def is_username_taken(self, username: str) -> bool:
        """Return True if the username is already used by another identity."""
        async with self.session() as db:
            result = await db.execute(
                select(Identity.id).where(Identity.username == username).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def create_identity(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        username: str | None = None,
        password_hash: str | None = None,
        is_platform_admin: bool = False,
        email_verified: bool = False,
    ) -> Identity:
        """Create and flush a new Identity row."""
        normalized_phone = re.sub(r"[\s\-\+]", "", phone) if phone else None
        async with self.session() as db:
            identity = Identity(
                email=email,
                phone=normalized_phone,
                username=username,
                password_hash=password_hash,
                is_platform_admin=is_platform_admin,
                email_verified=email_verified,
            )
            db.add(identity)
            await db.flush()
            return identity


identity_dao = IdentityDAO()
