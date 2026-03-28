"""SSO (Single Sign-On) service for enterprise user authentication.

This module handles SSO-based login, user matching, and tenant association.
"""

import re
import uuid
from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.models.user import User


class SSOService:
    """Service for handling SSO authentication flows."""

    # Common email domain to tenant mapping hints
    DOMAIN_TENANT_HINTS: dict[str, str] = {}

    async def match_user_by_email(
        self, db: AsyncSession, email: str, tenant_id: str | None = None
    ) -> User | None:
        """Find existing user by email address.

        Args:
            db: Database session
            email: User email address
            tenant_id: Optional tenant ID to scope the search

        Returns:
            User if found, None otherwise
        """
        query = select(User).where(User.email.ilike(f"%{email}%"))

        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def match_user_by_mobile(
        self, db: AsyncSession, mobile: str, tenant_id: str | None = None
    ) -> User | None:
        """Find existing user by mobile phone number.

        Args:
            db: Database session
            mobile: Mobile phone number
            tenant_id: Optional tenant ID to scope the search

        Returns:
            User if found, None otherwise
        """
        # Normalize mobile number (remove spaces, dashes, etc.)
        normalized_mobile = re.sub(r"[\s\-\+]", "", mobile)

        query = select(User).where(User.primary_mobile.ilike(f"%{normalized_mobile}%"))

        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def auto_associate_tenant(self, db: AsyncSession, email: str) -> str | None:
        """Detect tenant based on email domain.

        Args:
            db: Database session
            email: User email address

        Returns:
            Tenant ID if found, None otherwise
        """
        if not email or "@" not in email:
            return None

        domain = email.split("@")[1].lower()

        # Check domain hints first
        if domain in self.DOMAIN_TENANT_HINTS:
            return self.DOMAIN_TENANT_HINTS[domain]

        # Try to find tenant by custom domain
        result = await db.execute(
            select(Tenant).where(Tenant.custom_domain.ilike(f"%{domain}%"))
        )
        tenant = result.scalar_one_or_none()

        if tenant:
            return str(tenant.id)

        # Try to find tenant by matching tenant name/domain
        result = await db.execute(
            select(Tenant).where(
                or_(
                    Tenant.name.ilike(f"%{domain.split('.')[0]}%"),
                    Tenant.domain.ilike(f"%{domain}%"),
                )
            )
        )
        tenant = result.scalar_one_or_none()

        if tenant:
            return str(tenant.id)

        return None

    async def resolve_user_identity(
        self, db: AsyncSession, provider_user_id: str, provider_type: str, tenant_id: str | None = None
    ) -> User | None:
        """Resolve user from external identity via OrgMember.

        Args:
            db: Database session
            provider_user_id: User ID in the external system (unionid or userid)
            provider_type: Type of provider (feishu, dingtalk, etc.)
            tenant_id: Optional tenant ID to scope the provider search

        Returns:
            User if found via OrgMember, None otherwise
        """
        from app.models.org import OrgMember

        # Get provider
        query = select(IdentityProvider).where(IdentityProvider.provider_type == provider_type)
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)
            
        result = await db.execute(query)
        provider = result.scalar_one_or_none()

        if not provider:
            return None

        # Find OrgMember by unionid, external_id, or open_id
        # For Feishu/DingTalk we often use unionid, for WeCom we use external_id (userid)
        member_query = select(OrgMember).where(
            OrgMember.provider_id == provider.id,
            OrgMember.status == "active",
            or_(
                OrgMember.unionid == provider_user_id,
                OrgMember.external_id == provider_user_id,
                OrgMember.open_id == provider_user_id
            )
        )
        member_result = await db.execute(member_query)
        member = member_result.scalar_one_or_none()

        if not member or not member.user_id:
            return None

        # Get user
        user_result = await db.execute(select(User).where(User.id == member.user_id))
        return user_result.scalar_one_or_none()

    async def link_identity(
        self,
        db: AsyncSession,
        user_id: str,
        provider_type: str,
        provider_user_id: str,
        identity_data: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> Any:
        """Link an external identity to an existing user via OrgMember.

        Args:
            db: Database session
            user_id: User ID to link to
            provider_type: Type of provider
            provider_user_id: User ID in the external system
            identity_data: Additional identity data (unused now, stored in OrgMember if needed)
            tenant_id: Optional tenant ID for provider lookup

        Returns:
            The linked OrgMember
        """
        from app.models.org import OrgMember

        # Get or create provider
        query = select(IdentityProvider).where(
            IdentityProvider.provider_type == provider_type,
            IdentityProvider.tenant_id == tenant_id
        )
            
        result = await db.execute(query)
        provider = result.scalar_one_or_none()

        if not provider:
            raise ValueError(f"Provider {provider_type} not found for tenant {tenant_id}")

        # Check if OrgMember already exists (only active members)
        member_query = select(OrgMember).where(
            OrgMember.provider_id == provider.id,
            OrgMember.status == "active",
            or_(
                OrgMember.unionid == provider_user_id,
                OrgMember.external_id == provider_user_id,
                OrgMember.open_id == provider_user_id
            )
        )
        member_result = await db.execute(member_query)
        member = member_result.scalar_one_or_none()

        if member:
            # Link existing member to user
            member.user_id = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        else:
            # Create a shell OrgMember if not synced yet (though usually they should exist)
            member_name = identity_data.get("name") if identity_data else None
            if not member_name:
                # Try to get name from user_info if passed differently
                member_name = identity_data.get("display_name") if identity_data else None
            member = OrgMember(
                name=member_name or f"{provider_type.capitalize()} User {provider_user_id[:8]}",
                email=identity_data.get("email") if identity_data else None,
                provider_id=provider.id,
                user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                tenant_id=tenant_id,
                external_id=provider_user_id,
                unionid=provider_user_id if provider_type != "wecom" else None
            )
            db.add(member)
        
        await db.flush()
        return member

    async def unlink_identity(
        self, db: AsyncSession, user_id: str, provider_type: str, tenant_id: str | None = None
    ) -> bool:
        """Unlink an external identity (OrgMember) from a user.

        Args:
            db: Database session
            user_id: User ID
            provider_type: Type of provider to unlink
            tenant_id: Optional tenant ID

        Returns:
            True if unlinked, False if not found
        """
        from app.models.org import OrgMember

        # Get provider
        query = select(IdentityProvider).where(IdentityProvider.provider_type == provider_type)
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)
            
        result = await db.execute(query)
        provider = result.scalar_one_or_none()

        if not provider:
            return False

        # Find OrgMember
        mid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        member_result = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == mid,
                OrgMember.provider_id == provider.id,
            )
        )
        member = member_result.scalar_one_or_none()

        if not member:
            return False

        member.user_id = None
        await db.flush()

        return True

    async def check_duplicate_identity(
        self, db: AsyncSession, provider_type: str, provider_user_id: str, tenant_id: str | None = None
    ) -> User | None:
        """Check if an external identity is already linked to another user.

        Args:
            db: Database session
            provider_type: Type of provider
            provider_user_id: User ID in the external system
            tenant_id: Optional tenant ID

        Returns:
            Existing user if identity is already linked, None otherwise
        """
        return await self.resolve_user_identity(db, provider_user_id, provider_type, tenant_id)

    def add_domain_hint(self, domain: str, tenant_id: str):
        """Add a domain to tenant mapping hint.

        Args:
            domain: Email domain (e.g., "company.com")
            tenant_id: Associated tenant ID
        """
        self.DOMAIN_TENANT_HINTS[domain.lower()] = tenant_id


# Global SSO service instance
sso_service = SSOService()