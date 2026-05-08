"""Registration service for user account creation with SSO support.

This module handles user registration including:
- Email domain-based tenant detection
- SSO-based registration flow
- Duplicate identity detection
"""

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import hash_password
from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.models.user import User, Identity
from app.services.sso_service import sso_service
from loguru import logger


class RegistrationService:
    """Service for handling user registration flows."""

    async def ensure_identity_provider(
        self,
        db: AsyncSession,
        provider_type: str,
        tenant_id: uuid.UUID | None,
        *,
        name: str | None = None,
        sso_login_enabled: bool = False,
    ) -> IdentityProvider:
        """Get or create an identity provider record for a tenant."""
        query = select(IdentityProvider).where(
            IdentityProvider.provider_type == provider_type,
        )
        if tenant_id is None:
            query = query.where(IdentityProvider.tenant_id.is_(None))
        else:
            query = query.where(IdentityProvider.tenant_id == tenant_id)

        result = await db.execute(query)
        provider = result.scalar_one_or_none()
        if provider:
            return provider

        provider = IdentityProvider(
            provider_type=provider_type,
            name=name or provider_type.capitalize(),
            is_active=True,
            sso_login_enabled=sso_login_enabled,
            config={},
            tenant_id=tenant_id,
        )
        db.add(provider)
        await db.flush()
        return provider

    async def detect_tenant_by_email(self, db: AsyncSession, email: str) -> Tenant | None:
        """Detect tenant based on email domain.

        Args:
            db: Database session
            email: User email address

        Returns:
            Tenant if found by domain match, None otherwise
        """
        if not email or "@" not in email:
            return None

        domain = email.split("@")[1].lower()

        # Try to find tenant by custom domain
        result = await db.execute(
            select(Tenant).where(
                Tenant.sso_domain.ilike(f"%{domain}%"),
                Tenant.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def check_duplicate_identity(
        self,
        db: AsyncSession,
        email: str | None = None,
        mobile: str | None = None,
    ) -> dict[str, Any]:
        """Check for existing identities or tenant-users that might conflict.

        Args:
            db: Database session
            email: Email address
            mobile: Mobile phone
            username: Username
            tenant_id: Optional tenant to scope the search (for tenant-user conflicts)

        Returns:
            Dict with conflict information
        """
        conflicts = []

        # 1. Check Global Identity Conflicts
        if email:
            ident_result = await db.execute(select(Identity).where(Identity.email == email))
            if ident_result.scalar_one_or_none():
                conflicts.append({
                    "type": "email",
                    "scope": "global",
                    "message": "Email already registered",
                })
        
        if mobile:
            normalized_mobile = re.sub(r"[\s\-\+]", "", mobile)
            ident_result = await db.execute(select(Identity).where(Identity.phone == normalized_mobile))
            if ident_result.scalar_one_or_none():
                conflicts.append({
                    "type": "mobile",
                    "scope": "global",
                    "message": "Mobile already registered",
                })

        return {
            "has_conflict": len(conflicts) > 0,
            "conflicts": conflicts,
        }

    async def find_or_create_identity(
        self,
        db: AsyncSession,
        email: str | None = None,
        phone: str | None = None,
        username: str | None = None,
        password: str | None = None,
        is_platform_admin: bool = False,
    ) -> Identity:
        """Find an existing identity or create a new one.

        Security note: only email and phone are authoritative identity claims.
        Username is NOT used as a lookup key — it is just a display name and
        cannot prove ownership. Using it as a fallback would allow account
        takeover when two users share the same email prefix (e.g. alice@gmail.com
        and alice@yahoo.com both produce username 'alice').
        """
        identity = None

        # Match by email (primary ownership claim)
        if email:
            res = await db.execute(select(Identity).where(Identity.email == email))
            identity = res.scalar_one_or_none()

        # Match by phone (secondary ownership claim)
        if not identity and phone:
            normalized_phone = re.sub(r"[\s\-\+]", "", phone)
            res = await db.execute(select(Identity).where(Identity.phone == normalized_phone))
            identity = res.scalar_one_or_none()

        # Username is intentionally NOT used as a lookup key.
        # If we cannot establish ownership via email or phone, treat this as a
        # new identity to avoid returning another user's record.

        if identity:
            # Auto-verify if SMTP is not configured anywhere (env or DB)
            from app.services.system_email_service import resolve_email_config_async
            email_config = await resolve_email_config_async(db)
            if not email_config:
                if not identity.email_verified:
                    identity.email_verified = True
                    db.add(identity)
            return identity

        # Check if SMTP is configured anywhere (env or DB) for auto-verification
        from app.services.system_email_service import resolve_email_config_async
        email_config = await resolve_email_config_async(db)
        is_verified = not email_config  # Auto-verify only if no SMTP configured

        # Resolve a safe username: if the desired username is already taken by
        # another identity, append a short random hex suffix to avoid collisions
        # without blocking the registration.
        final_username = username
        if username:
            existing_res = await db.execute(
                select(Identity).where(Identity.username == username)
            )
            if existing_res.scalar_one_or_none():
                final_username = f"{username}_{uuid.uuid4().hex[:6]}"
                logger.info(
                    "Username '%s' already taken; assigned '%s' to new identity",
                    username,
                    final_username,
                )

        # Create new identity
        normalized_phone = re.sub(r"[\s\-\+]", "", phone) if phone else None
        identity = Identity(
            email=email,
            phone=normalized_phone,
            username=final_username,
            password_hash=hash_password(password) if password else None,
            is_platform_admin=is_platform_admin,
            email_verified=is_verified,
        )
        db.add(identity)
        await db.flush()
        return identity

    async def create_user_with_identity(
        self,
        db: AsyncSession,
        identity: Identity,
        display_name: str | None = None,
        role: str = "member",
        tenant_id: uuid.UUID | None = None,
        registration_source: str = "web",
    ) -> User:
        """Create a new tenant-specific user linked to an identity.

        Args:
            db: Database session
            identity: The global identity
            display_name: Tenant-specific display name
            role: Role within the tenant
            tenant_id: Tenant ID
            registration_source: Source of registration

        Returns:
            Created User (tenant-user)
        """
        # Ensure unique display name / username within tenant if needed
        # (Using display_name or identity info)
        name = display_name or identity.username or "User"

        # Check if SMTP is configured anywhere (env or DB) for auto-activation
        from app.services.system_email_service import resolve_email_config_async
        email_config = await resolve_email_config_async(db)
        is_active = identity.email_verified
        if not email_config:
            is_active = True  # Auto-activate if no SMTP configured

        # Create tenant-user record
        user = User(
            identity_id=identity.id,
            tenant_id=tenant_id,
            display_name=name,
            role=role,
            registration_source=registration_source,
            is_active=is_active or identity.is_platform_admin,
        )

        db.add(user)
        await db.flush()

        # Link to OrgMember if exists
        await self.bind_org_member(db, user)

        # Create Participant record
        from app.models.participant import Participant
        db.add(Participant(
            type="user",
            ref_id=user.id,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
        ))

        await db.flush()
        return user

    async def handle_sso_registration(
        self,
        db: AsyncSession,
        provider_type: str,
        provider_user_id: str,
        user_info: dict,
        existing_user: User | None = None,
    ) -> tuple[User, bool]:
        """Handle SSO-based registration flow.

        If existing_user is provided, links the identity to that user.
        Otherwise, creates a new user or returns existing one.

        Args:
            db: Database session
            provider_type: Provider type (feishu, dingtalk, etc.)
            provider_user_id: User ID in external system
            user_info: User info from provider
            existing_user: Optional existing user to link to

        Returns:
            Tuple of (user, is_new)
        """
        # Try to detect tenant from email
        email = user_info.get("email", "")
        tenant = None
        tenant_id = None
        if email:
            tenant = await self.detect_tenant_by_email(db, email)
            tenant_id = tenant.id if tenant else None

        # Check if identity already exists
        lookup_provider_user_id = user_info.get("union_id") or user_info.get("unionId") or provider_user_id
        existing = await sso_service.resolve_user_identity(
            db,
            lookup_provider_user_id,
            provider_type,
            tenant_id=tenant_id,
            identity_data=user_info,
        )

        if existing:
            # Identity already linked
            return existing, False

        if existing_user:
            # Link to existing user
            await sso_service.link_identity(
                db,
                str(existing_user.id),
                provider_type,
                lookup_provider_user_id,
                user_info,
                tenant_id=str(existing_user.tenant_id) if existing_user.tenant_id else tenant_id,
            )
            return existing_user, False

        # (moved up)
        pass

        # Step 2: Ensure Identity exists
        # Generate username from email or provider ID (fallback to open_id)
        effective_id = provider_user_id or user_info.get("open_id") or user_info.get("union_id") or uuid.uuid4().hex[:8]
        username = email.split("@")[0] if email else f"{provider_type}_{effective_id[:8]}"

        identity = await self.find_or_create_identity(
            db,
            email=email,
            phone=user_info.get("mobile") or user_info.get("phone"),
            username=username,
            password=effective_id, # Placeholder for SSO users
        )


        # Step 3: Create User linked to Identity
        user = await self.create_user_with_identity(
            db,
            identity=identity,
            display_name=user_info.get("name", username),
            registration_source=provider_type,
            tenant_id=tenant_id,
        )


        return user, True

    async def register_with_sso(
        self,
        db: AsyncSession,
        provider_type: str,
        code: str,
        auth_provider,
    ) -> tuple[User, bool, str | None]:
        """Register or login user via SSO.

        Args:
            db: Database session
            provider_type: Provider type
            code: OAuth authorization code
            auth_provider: Auth provider instance

        Returns:
            Tuple of (user, is_new, error_message)
        """
        try:
            # Exchange code for token
            token_data = await auth_provider.exchange_code_for_token(code)
            access_token = token_data.get("access_token")
            if not access_token:
                return None, False, "Failed to get access token from provider"

            # Get user info
            from app.services.auth_provider import ExternalUserInfo
            user_info_obj = await auth_provider.get_user_info(access_token)

            # Convert to dict
            user_info = {
                "name": user_info_obj.name,
                "email": user_info_obj.email,
                "avatar_url": user_info_obj.avatar_url,
                "mobile": user_info_obj.mobile,
                "raw_data": user_info_obj.raw_data,
            }

            # Try to detect tenant from email
            email_addr = user_info_obj.email
            tenant_id = None
            if email_addr:
                tenant = await self.detect_tenant_by_email(db, email_addr)
                tenant_id = tenant.id if tenant else None

            # Try to find existing user by identity
            lookup_provider_user_id = user_info_obj.provider_union_id or user_info_obj.provider_user_id
            existing_user = await sso_service.resolve_user_identity(
                db,
                lookup_provider_user_id,
                provider_type,
                tenant_id=tenant_id,
                identity_data=user_info,
            )

            if existing_user:
                # Update last login
                return existing_user, False, None

            # Also try matching by email
            if user_info_obj.email:
                existing_by_email = await sso_service.match_user_by_email(db, user_info_obj.email)
                if existing_by_email:
                    # Link identity to existing user
                    await sso_service.link_identity(
                        db,
                        str(existing_by_email.id),
                        provider_type,
                        lookup_provider_user_id,
                        user_info,
                        tenant_id=str(existing_by_email.tenant_id) if existing_by_email.tenant_id else tenant_id,
                    )
                    return existing_by_email, False, None

            # Create new user
            user, is_new = await self.handle_sso_registration(
                db,
                provider_type,
                lookup_provider_user_id,
                user_info,
            )

            # Bind to OrgMember via email/phone if possible
            await self.bind_org_member(db, user)

            return user, is_new, None

        except Exception as e:
            logger.exception("SSO registration failed for %s provider", provider_type)
            return None, False, f"SSO registration failed: {str(e)}"

    async def get_tenant_for_registration(
        self, db: AsyncSession, email: str | None = None, invitation_code: str | None = None
    ) -> tuple[Tenant | None, str]:
        """Determine tenant for new user registration.

        Args:
            db: Database session
            email: User email (for domain matching)
            invitation_code: Invitation code (for tenant association)

        Returns:
            Tuple of (tenant, error_message)
        """
        # First check invitation code
        if invitation_code:
            from app.models.invitation_code import InvitationCode
            result = await db.execute(
                select(InvitationCode).where(
                    InvitationCode.code == invitation_code,
                    InvitationCode.is_active == True,
                    InvitationCode.tenant_id.is_not(None),
                )
            )
            inv = result.scalar_one_or_none()
            if inv and inv.used_count < inv.max_uses:
                # Get tenant from invitation
                tenant_result = await db.execute(select(Tenant).where(Tenant.id == inv.tenant_id))
                tenant = tenant_result.scalar_one_or_none()
                if tenant and tenant.is_active:
                    return tenant, None
                return None, "Invitation code tenant is inactive"

        # Try email domain matching
        if email:
            tenant = await self.detect_tenant_by_email(db, email)
            if tenant:
                return tenant, None

        # No tenant association - user will need to create/join
        return None, None

    async def bind_org_member(self, db: AsyncSession, user: User) -> None:
        """Find and bind OrgMember to User based on email/phone and tenant_id.
        
        This establishes the link between a platform user and their entry in the
        synchronized organizational structure.
        """
        if not user.tenant_id:
            return

        from app.models.org import OrgMember
        member = await self._find_unbound_org_member_by_contact(db, user)
        if member:
            member.user_id = user.id
            if user.email and member.email != user.email:
                member.email = user.email
            elif not user.email and member.email:
                user.email = member.email

            if user.primary_mobile and member.phone != user.primary_mobile:
                member.phone = user.primary_mobile
            elif not user.primary_mobile and member.phone:
                user.primary_mobile = member.phone
            await db.flush()

            from app.services.okr_agent_hook import hook_new_org_member
            await hook_new_org_member(db, member.id, user.tenant_id)

        await self.ensure_web_org_member(db, user)

    async def _find_unbound_org_member_by_contact(
        self,
        db: AsyncSession,
        user: User,
    ):
        from app.models.org import OrgMember

        if user.email:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.email == user.email,
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            member = result.scalar_one_or_none()
            if member:
                return member

        if user.primary_mobile:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.phone == user.primary_mobile,
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            member = result.scalar_one_or_none()
            if member:
                return member

        return None

    async def ensure_web_org_member(self, db: AsyncSession, user: User):
        """Ensure the user has a dedicated platform OrgMember record in their tenant."""
        if not user.tenant_id:
            return None

        from app.models.org import OrgMember

        web_provider = await self.ensure_identity_provider(
            db,
            "web",
            user.tenant_id,
            name="Platform",
        )
        if web_provider.name == "Web":
            web_provider.name = "Platform"

        result = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == user.id,
                OrgMember.tenant_id == user.tenant_id,
                OrgMember.provider_id == web_provider.id,
            ).limit(1)
        )
        member = result.scalar_one_or_none()

        if not member and user.email:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.email == user.email,
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.provider_id == web_provider.id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            member = result.scalar_one_or_none()

        if not member and user.primary_mobile:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.phone == user.primary_mobile,
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.provider_id == web_provider.id,
                    OrgMember.user_id == None,
                ).limit(1)
            )
            member = result.scalar_one_or_none()

        created = False
        linked_existing = False
        if member:
            linked_existing = member.user_id is None
            member.user_id = user.id
        else:
            member = OrgMember(
                name=user.display_name or "User",
                email=user.email,
                phone=user.primary_mobile,
                provider_id=web_provider.id,
                title="Platform User",
                tenant_id=user.tenant_id,
                user_id=user.id,
                status="active",
            )
            db.add(member)
            created = True

        desired_name = user.display_name or member.name or "User"
        if desired_name and member.name != desired_name:
            member.name = desired_name
        if member.email != user.email:
            member.email = user.email
        if member.phone != user.primary_mobile:
            member.phone = user.primary_mobile
        if member.title in (None, "", "Web User"):
            member.title = "Platform User"

        await db.flush()

        if created or linked_existing:
            from app.services.okr_agent_hook import hook_new_org_member
            await hook_new_org_member(db, member.id, user.tenant_id)

        return member

    async def sync_org_member_contact_from_user(
        self,
        db: AsyncSession,
        user: User,
        *,
        sync_email: bool = False,
        sync_phone: bool = False,
    ) -> None:
        """Sync email/phone from User to linked OrgMember (user is source of truth)."""
        if not user.tenant_id or not (sync_email or sync_phone):
            return

        from app.models.org import OrgMember
        web_provider = await self.ensure_identity_provider(db, "web", user.tenant_id, name="Platform")
        if web_provider.name == "Web":
            web_provider.name = "Platform"

        result = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == user.id,
                OrgMember.tenant_id == user.tenant_id,
                OrgMember.provider_id == web_provider.id,
            )
        )
        members = result.scalars().all()
        if not members:
            return

        for member in members:
            if sync_email and member.email != user.email:
                member.email = user.email
            if sync_phone and member.phone != user.primary_mobile:
                member.phone = user.primary_mobile

        await db.flush()


# Global registration service
registration_service = RegistrationService()
