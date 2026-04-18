"""SSO (Single Sign-On) service for enterprise user authentication.

This module handles SSO-based login, user matching, and tenant association.
"""

import re
import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.models.user import Identity, User
from app.services.platform_service import platform_service


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
        # 1. Try direct match via Identity join
        query = (
            select(User)
            .join(User.identity)
            .where(Identity.email == email)
        )
        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)
        
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if user:
            return user
            
        # 2. If not found and tenant_id is provided, try to find an Identity
        if email:
            id_query = select(Identity).where(Identity.email == email)
            id_result = await db.execute(id_query)
            identity = id_result.scalar_one_or_none()
            if identity:
                # Find any user for this identity (representative)
                u_res = await db.execute(select(User).where(User.identity_id == identity.id).limit(1))
                return u_res.scalar_one_or_none()
                
        return None

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
        # Normalize mobile number
        normalized_mobile = re.sub(r"[\s\-\+]", "", mobile)
        if not normalized_mobile:
            return None

        # 1. Try direct match via Identity join
        query = (
            select(User)
            .join(User.identity)
            .where(Identity.phone == normalized_mobile)
        )
        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)
            
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        if user:
            return user

        # 2. Try Identity match
        id_query = select(Identity).where(Identity.phone == normalized_mobile)
        id_result = await db.execute(id_query)
        identity = id_result.scalar_one_or_none()
        if identity:
             u_res = await db.execute(select(User).where(User.identity_id == identity.id).limit(1))
             return u_res.scalar_one_or_none()

        return None

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
            select(Tenant).where(Tenant.sso_domain.ilike(f"%{domain}%"))
        )
        tenant = result.scalar_one_or_none()

        if tenant:
            return str(tenant.id)

        # Try to find tenant by matching tenant name
        result = await db.execute(
            select(Tenant).where(
                Tenant.name.ilike(f"%{domain.split('.')[0]}%")
            )
        )
        tenant = result.scalar_one_or_none()

        if tenant:
            return str(tenant.id)

        return None

    async def resolve_user_identity(
        self,
        db: AsyncSession,
        provider_user_id: str,
        provider_type: str,
        tenant_id: str | None = None,
        identity_data: dict[str, Any] | None = None,
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

        member = await self._find_identity_member(
            db,
            provider.id,
            provider_type,
            provider_user_id,
            identity_data,
        )

        if not member or not member.user_id:
            return None

        # Get user
        from sqlalchemy.orm import selectinload
        user_result = await db.execute(
            select(User).where(User.id == member.user_id).options(selectinload(User.identity))
        )
        return user_result.scalar_one_or_none()

    def _get_identity_payload(self, identity_data: dict[str, Any] | None) -> dict[str, Any]:
        if not identity_data:
            return {}
        raw_data = identity_data.get("raw_data")
        if isinstance(raw_data, dict):
            return raw_data
        return identity_data

    def _extract_identity_ids(
        self,
        provider_type: str,
        provider_user_id: str,
        identity_data: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, str | None]:
        payload = self._get_identity_payload(identity_data)
        identity_data = identity_data or {}

        raw_open_id = (
            payload.get("open_id")
            or payload.get("openId")
            or identity_data.get("open_id")
            or identity_data.get("openId")
        )
        raw_union_id = (
            payload.get("union_id")
            or payload.get("unionId")
            or identity_data.get("union_id")
            or identity_data.get("unionId")
        )

        external_id = None
        if provider_type == "feishu":
            external_id = payload.get("user_id")
        elif provider_type == "dingtalk":
            external_id = payload.get("userid") or payload.get("staffId")
        elif provider_type == "wecom":
            external_id = provider_user_id

        open_id = (raw_open_id or "").strip() or None
        union_id = (raw_union_id or "").strip() or None
        external_id = (external_id or "").strip() or None
        return union_id, open_id, external_id

    def _identity_lookup_chain(
        self,
        provider_type: str,
        provider_user_id: str,
        identity_data: dict[str, Any] | None,
    ) -> list[tuple[str, str]]:
        raw_union_id, raw_open_id, raw_external_id = self._extract_identity_ids(
            provider_type, provider_user_id, identity_data
        )

        lookup_chain: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(field: str, value: str | None) -> None:
            normalized = (value or "").strip()
            key = (field, normalized)
            if not normalized or key in seen:
                return
            seen.add(key)
            lookup_chain.append(key)

        add("unionid", raw_union_id)
        add("external_id", raw_external_id)
        add("open_id", raw_open_id)

        if not lookup_chain:
            fallback_id = (provider_user_id or "").strip()
            if provider_type == "wecom":
                add("external_id", fallback_id)
            else:
                add("unionid", fallback_id)
                add("external_id", fallback_id)
                add("open_id", fallback_id)

        return lookup_chain

    async def _find_identity_member(
        self,
        db: AsyncSession,
        provider_id: uuid.UUID,
        provider_type: str,
        provider_user_id: str,
        identity_data: dict[str, Any] | None = None,
    ):
        from app.models.org import OrgMember

        for field, lookup_value in self._identity_lookup_chain(provider_type, provider_user_id, identity_data):
            column = getattr(OrgMember, field)
            member_result = await db.execute(
                select(OrgMember).where(
                    OrgMember.provider_id == provider_id,
                    OrgMember.status == "active",
                    column == lookup_value,
                )
            )
            member = member_result.scalar_one_or_none()
            if member:
                return member

        return None

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

        When an OrgMember already exists (e.g. from org-sync), this also
        enriches its profile fields with fresh SSO data so placeholder
        records become fully hydrated over time.

        Args:
            db: Database session
            user_id: User ID to link to
            provider_type: Type of provider
            provider_user_id: User ID in the external system
            identity_data: Raw data from the provider (ExternalUserInfo.raw_data);
                           used for passive profile enrichment.
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

        uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id

        raw_union_id, raw_open_id, raw_external_id = self._extract_identity_ids(
            provider_type, provider_user_id, identity_data
        )
        member = await self._find_identity_member(
            db,
            provider.id,
            provider_type,
            provider_user_id,
            identity_data,
        )

        if member:
            # Always link user
            member.user_id = uid

            if raw_external_id and not member.external_id:
                member.external_id = raw_external_id

            if raw_open_id and not member.open_id:
                member.open_id = raw_open_id

            if raw_union_id and member.unionid != raw_union_id:
                if not member.unionid or member.unionid in {provider_user_id, member.open_id, member.external_id}:
                    member.unionid = raw_union_id

            # Passive identity enrichment: update profile fields from SSO data.
            # OrgMember records created by org-sync may have placeholder values
            # (e.g. name=userid, no avatar/email). We fill them in here so they
            # become accurate after the user's first SSO login, without needing
            # IP-whitelisted batch calls.
            if identity_data:
                incoming_name = (
                    identity_data.get("name")
                    or identity_data.get("display_name")
                )
                # Only overwrite name if the current value looks like a placeholder
                # (e.g. was set to the raw userid during degraded org sync)
                is_placeholder_name = (
                    not member.name
                    or member.name == member.external_id
                    or member.name == provider_user_id
                    or member.name.startswith(f"{provider_type.capitalize()} User")
                )
                if incoming_name and is_placeholder_name:
                    member.name = incoming_name

                incoming_email = identity_data.get("email") or identity_data.get("biz_mail")
                if incoming_email and not member.email:
                    member.email = incoming_email

                incoming_avatar = identity_data.get("avatar")
                if incoming_avatar and not member.avatar_url:
                    member.avatar_url = incoming_avatar

                incoming_mobile = identity_data.get("mobile")
                if incoming_mobile and not member.phone:
                    member.phone = incoming_mobile

        else:
            # Create a shell OrgMember if not synced yet.
            # This handles organizations that skip org-sync and rely purely on SSO.
            member_name = (
                (identity_data.get("name") or identity_data.get("display_name"))
                if identity_data else None
            )
            member = OrgMember(
                name=member_name or f"{provider_type.capitalize()} User {provider_user_id[:8]}",
                email=(identity_data.get("email") or identity_data.get("biz_mail")) if identity_data else None,
                avatar_url=identity_data.get("avatar") if identity_data else None,
                phone=identity_data.get("mobile") if identity_data else None,
                provider_id=provider.id,
                user_id=uid,
                tenant_id=tenant_id,
                external_id=raw_external_id,
                unionid=raw_union_id if provider_type != "wecom" else None,
                open_id=raw_open_id,
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
        self,
        db: AsyncSession,
        provider_type: str,
        provider_user_id: str,
        tenant_id: str | None = None,
        identity_data: dict[str, Any] | None = None,
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
        return await self.resolve_user_identity(
            db,
            provider_user_id,
            provider_type,
            tenant_id,
            identity_data=identity_data,
        )

    async def validate_sso_enablement(self, db: AsyncSession, tenant_id: uuid.UUID) -> bool:
        """Check if SSO can be enabled for this tenant under IP restrictions.

        Only checks when THIS tenant doesn't have SSO enabled yet.
        If tenant already has sso_enabled=True, allows without checking.

        Returns True if allowed, False if another tenant already has SSO enabled on an IP base.
        """
        # First check if this tenant already has SSO enabled
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if tenant and tenant.sso_enabled:
            # Already has SSO enabled, can freely toggle providers
            return True

        # This tenant doesn't have SSO enabled yet, check IP restriction
        base_url = await platform_service.get_public_base_url(db)

        # Parse host
        parts = base_url.split("://")
        if len(parts) < 2:
            return True  # Conservative default

        host = parts[1].split(":")[0].split("/")[0]

        if not platform_service.is_ip_address(host):
            return True

        # IP Address: only ONE tenant in the whole system can have SSO enabled.
        # Check if any *other* tenant has an active SSO-enabled provider.
        query = select(IdentityProvider).where(
            IdentityProvider.sso_login_enabled == True,
            IdentityProvider.is_active == True,
            IdentityProvider.tenant_id != tenant_id,
        )
        result = await db.execute(query)
        other_providers = result.scalars().all()

        if other_providers:
            # Collect conflicting tenant names
            conflict_names = []
            for other_provider in other_providers:
                tenant_query = await db.execute(select(Tenant).where(Tenant.id == other_provider.tenant_id))
                conflict_tenant = tenant_query.scalar_one_or_none()
                name = conflict_tenant.name if conflict_tenant else str(other_provider.tenant_id)
                conflict_names.append(f"'{name}'")
            conflict_str = ", ".join(conflict_names)
            logger.warning(f"[SSO] IP conflict: tenant_id={tenant_id} cannot enable SSO, other tenants already have SSO enabled on IP base: {conflict_str}")
        return len(other_providers) == 0

    def add_domain_hint(self, domain: str, tenant_id: str):
        """Add a domain to tenant mapping hint.

        Args:
            domain: Email domain (e.g., "company.com")
            tenant_id: Associated tenant ID
        """
        self.DOMAIN_TENANT_HINTS[domain.lower()] = tenant_id


# Global SSO service instance
sso_service = SSOService()
