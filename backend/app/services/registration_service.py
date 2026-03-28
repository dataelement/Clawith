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

from app.core.security import hash_password
from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.models.user import User
from app.services.sso_service import sso_service
from loguru import logger


class RegistrationService:
    """Service for handling user registration flows."""

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
                or_(
                    Tenant.custom_domain.ilike(f"%{domain}%"),
                    Tenant.domain.ilike(f"%{domain}%"),
                ),
                Tenant.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def check_duplicate_identity(
        self,
        db: AsyncSession,
        email: str | None = None,
        mobile: str | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Check for existing identities that might conflict with registration.

        Args:
            db: Database session
            email: User email
            mobile: User mobile
            tenant_id: Optional tenant to scope the search

        Returns:
            Dict with conflict information:
            {
                "has_conflict": bool,
                "conflicts": [
                    {"type": "email|mobile|identity", "existing_user_id": str}
                ]
            }
        """
        conflicts = []

        # Check email conflicts
        if email:
            result = await db.execute(
                select(User).where(User.email.ilike(f"%{email}%"))
            )
            existing = result.scalar_one_or_none()
            if existing:
                conflicts.append({
                    "type": "email",
                    "existing_user_id": str(existing.id),
                    "message": "Email already registered",
                })

        # Check mobile conflicts
        if mobile:
            normalized_mobile = re.sub(r"[\s\-\+]", "", mobile)
            result = await db.execute(
                select(User).where(
                    and_(
                        User.primary_mobile.ilike(f"%{normalized_mobile}%"),
                        User.tenant_id == tenant_id if tenant_id else True,
                    )
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                conflicts.append({
                    "type": "mobile",
                    "existing_user_id": str(existing.id),
                    "message": "Mobile already registered",
                })

        return {
            "has_conflict": len(conflicts) > 0,
            "conflicts": conflicts,
        }

    async def create_user_with_identity(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        display_name: str | None = None,
        provider_type: str | None = None,
        provider_user_id: str | None = None,
        provider_data: dict | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Create a new user with optional external identity.

        Args:
            db: Database session
            username: Username
            email: Email address
            password: Plain text password
            display_name: Display name
            provider_type: External provider type (feishu, dingtalk, etc.)
            provider_user_id: User ID in external system
            provider_data: Raw data from provider
            tenant_id: Tenant ID to assign user to

        Returns:
            Created User
        """
        # Ensure unique username
        existing = await db.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none():
            username = f"{username}_{uuid.uuid4().hex[:6]}"

        # Create user
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            display_name=display_name or username,
            registration_source=provider_type or "web",
            tenant_id=tenant_id,
        )

        db.add(user)
        await db.flush()

        # Link to OrgMember if exists (bind platform user to org structure)
        await self.bind_org_member(db, user)

        # Create Participant identity
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
        existing = await sso_service.resolve_user_identity(db, provider_user_id, provider_type, tenant_id=tenant_id)

        if existing:
            # Identity already linked
            return existing, False

        if existing_user:
            # Link to existing user
            await sso_service.link_identity(
                db,
                str(existing_user.id),
                provider_type,
                provider_user_id,
                user_info,
                tenant_id=str(existing_user.tenant_id) if existing_user.tenant_id else tenant_id,
            )
            return existing_user, False

        # (moved up)
        pass

        # Generate username from email or provider ID
        username = email.split("@")[0] if email else f"{provider_type}_{provider_user_id[:8]}"

        user = await self.create_user_with_identity(
            db,
            username=username,
            email=email or f"{username}@{provider_type}.local",
            password=provider_user_id,  # Placeholder for SSO users
            display_name=user_info.get("name", username),
            provider_type=provider_type,
            provider_user_id=provider_user_id,
            provider_data=user_info,
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
            existing_user = await sso_service.resolve_user_identity(
                db, user_info_obj.provider_user_id, provider_type, tenant_id=tenant_id
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
                        user_info_obj.provider_user_id,
                        user_info,
                        tenant_id=str(existing_by_email.tenant_id) if existing_by_email.tenant_id else tenant_id,
                    )
                    return existing_by_email, False, None

            # Create new user
            user, is_new = await self.handle_sso_registration(
                db,
                provider_type,
                user_info_obj.provider_user_id,
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
            from app.models.invitation import InvitationCode
            result = await db.execute(
                select(InvitationCode).where(
                    InvitationCode.code == invitation_code,
                    InvitationCode.uses_left > 0,
                )
            )
            inv = result.scalar_one_or_none()
            if inv:
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
        
        member = None

        # Prefer email match
        if user.email:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.email.ilike(user.email),
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.user_id == None
                )
            )
            member = result.scalar_one_or_none()

        # Fallback to phone match
        if not member and user.primary_mobile:
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.phone == user.primary_mobile,
                    OrgMember.tenant_id == user.tenant_id,
                    OrgMember.user_id == None
                )
            )
            member = result.scalar_one_or_none()
        
        if member:
            member.user_id = user.id
            
            # Sync email/phone both ways (prefer user if provided)
            if user.email and member.email != user.email:
                member.email = user.email
            elif not user.email and member.email:
                user.email = member.email

            if user.primary_mobile and member.phone != user.primary_mobile:
                member.phone = user.primary_mobile
            elif not user.primary_mobile and member.phone:
                user.primary_mobile = member.phone
            
            await db.flush()

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

        result = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == user.id,
                OrgMember.tenant_id == user.tenant_id,
            )
        )
        member = result.scalar_one_or_none()
        if not member:
            return

        if sync_email and member.email != user.email:
            member.email = user.email
        if sync_phone and member.phone != user.primary_mobile:
            member.phone = user.primary_mobile

        await db.flush()


# Global registration service
registration_service = RegistrationService()
