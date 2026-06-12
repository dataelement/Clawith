"""Registration service for user account creation with SSO support.

This module handles user registration including:
- Email domain-based tenant detection
- SSO-based registration flow
- Duplicate identity detection
"""

import re
import uuid
from typing import Any

from app.config import get_settings
from app.core.security import hash_password_async
from app.dao import (
    identity_dao,
    identity_provider_dao,
    invitation_code_dao,
    org_member_dao,
    participant_dao,
    tenant_dao,
    user_dao,
)
from app.models.identity import IdentityProvider
from app.models.tenant import Tenant
from app.models.user import User, Identity
from app.services.sso_service import sso_service
from app.services.system_email_service import resolve_email_config_async
from loguru import logger


class RegistrationService:
    """Service for handling user registration flows."""

    # ── Identity provider ────────────────────────────────────────────────────

    async def ensure_identity_provider(
        self,
        provider_type: str,
        tenant_id: uuid.UUID | None,
        *,
        name: str | None = None,
        sso_login_enabled: bool = False,
    ) -> IdentityProvider:
        """Get or create an identity provider record for a tenant."""
        return await identity_provider_dao.get_or_create(
            provider_type,
            tenant_id,
            name=name,
            sso_login_enabled=sso_login_enabled,
        )

    # ── Tenant detection ─────────────────────────────────────────────────────

    async def detect_tenant_by_email(self, email: str) -> Tenant | None:
        """Detect tenant based on email domain."""
        if not email or "@" not in email:
            return None
        domain = email.split("@")[1].lower()
        return await tenant_dao.get_by_sso_domain(domain)

    # ── Duplicate check ──────────────────────────────────────────────────────

    async def check_duplicate_identity(
        self,
        email: str | None = None,
        mobile: str | None = None,
    ) -> dict[str, Any]:
        """Check for existing identities that might conflict.

        Returns:
            Dict with ``has_conflict`` bool and ``conflicts`` list.
        """
        conflicts = []

        if email and await identity_dao.get_by_email(email):
            conflicts.append({
                "type": "email",
                "scope": "global",
                "message": "Email already registered",
            })

        if mobile:
            normalized = re.sub(r"[\s\-\+]", "", mobile)
            if await identity_dao.get_by_phone(normalized):
                conflicts.append({
                    "type": "mobile",
                    "scope": "global",
                    "message": "Mobile already registered",
                })

        return {"has_conflict": len(conflicts) > 0, "conflicts": conflicts}

    # ── Identity find / create ───────────────────────────────────────────────

    async def find_or_create_identity(
        self,
        email: str | None = None,
        phone: str | None = None,
        username: str | None = None,
        password: str | None = None,
        is_platform_admin: bool = False,
        email_config: Any = None,
        password_hash: str | None = None,
    ) -> Identity:
        """Find an existing identity or create a new one.

        Security note: only email and phone are authoritative identity claims.
        """
        identity: Identity | None = None

        # Match by email (primary ownership claim)
        if email:
            identity = await identity_dao.get_by_email(email)

        # Match by phone (secondary ownership claim)
        if not identity and phone:
            identity = await identity_dao.get_by_phone(phone)

        if identity:
            # Auto-verify if SMTP is not configured
            if not email_config:
                email_config = await resolve_email_config_async()
            if not email_config and not identity.email_verified:
                await identity_dao.update(db_obj=identity, obj_in={"email_verified": True})
            return identity

        # Determine verified status
        if not email_config:
            email_config = await resolve_email_config_async()
        is_verified = not email_config  # Auto-verify only when no SMTP configured

        # Resolve a safe, unique username
        final_username = username
        if username and await identity_dao.is_username_taken(username):
            final_username = f"{username}_{uuid.uuid4().hex[:6]}"
            logger.info(
                "Username '%s' already taken; assigned '%s' to new identity",
                username,
                final_username,
            )

        # Hash password if not pre-hashed
        if not password_hash and password:
            password_hash = await hash_password_async(password)

        return await identity_dao.create_identity(
            email=email,
            phone=phone,
            username=final_username,
            password_hash=password_hash,
            is_platform_admin=is_platform_admin,
            email_verified=is_verified,
        )

    # ── User create ──────────────────────────────────────────────────────────

    async def create_user_with_identity(
        self,
        identity: Identity,
        display_name: str | None = None,
        role: str = "member",
        tenant_id: uuid.UUID | None = None,
        registration_source: str = "web",
        email_config: Any = None,
    ) -> User:
        """Create a new tenant-specific user linked to an identity."""
        name = display_name or identity.username or "User"

        if not email_config:
            email_config = await resolve_email_config_async()

        is_active = identity.email_verified
        if not email_config:
            is_active = True  # Auto-activate when no SMTP configured

        user = await user_dao.create(obj_in={
            "identity_id": identity.id,
            "tenant_id": tenant_id,
            "display_name": name,
            "role": role,
            "registration_source": registration_source,
            "is_active": is_active or identity.is_platform_admin,
        })

        # Link to OrgMember if exists
        await self.bind_org_member(user)

        # Create Participant record
        await participant_dao.create_for_user(
            user.id,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
        )

        return user

    # ── SSO flows ────────────────────────────────────────────────────────────

    async def handle_sso_registration(
        self,
        provider_type: str,
        provider_user_id: str,
        user_info: dict,
        existing_user: User | None = None,
    ) -> tuple[User, bool]:
        """Handle SSO-based registration flow."""
        email = user_info.get("email", "")
        tenant_id = None
        if email:
            tenant = await self.detect_tenant_by_email(email)
            tenant_id = tenant.id if tenant else None

        lookup_provider_user_id = (
            user_info.get("union_id") or user_info.get("unionId") or provider_user_id
        )
        async with identity_dao.session() as db:
            existing = await sso_service.resolve_user_identity(
                db,
                lookup_provider_user_id,
                provider_type,
                tenant_id=tenant_id,
                identity_data=user_info,
            )
            if existing:
                return existing, False

            if existing_user:
                await sso_service.link_identity(
                    db,
                    str(existing_user.id),
                    provider_type,
                    lookup_provider_user_id,
                    user_info,
                    tenant_id=str(existing_user.tenant_id) if existing_user.tenant_id else tenant_id,
                )
                return existing_user, False

        # Create new Identity + User
        effective_id = (
            provider_user_id
            or user_info.get("open_id")
            or user_info.get("union_id")
            or uuid.uuid4().hex[:8]
        )
        username = email.split("@")[0] if email else f"{provider_type}_{effective_id[:8]}"

        identity = await self.find_or_create_identity(
            email=email,
            phone=user_info.get("mobile") or user_info.get("phone"),
            username=username,
            password=effective_id,
        )

        user = await self.create_user_with_identity(
            identity=identity,
            display_name=user_info.get("name", username),
            registration_source=provider_type,
            tenant_id=tenant_id,
        )

        return user, True

    async def register_with_sso(
        self,
        provider_type: str,
        code: str,
        auth_provider,
    ) -> tuple[User, bool, str | None]:
        """Register or login user via SSO."""
        try:
            token_data = await auth_provider.exchange_code_for_token(code)
            access_token = token_data.get("access_token")
            if not access_token:
                return None, False, "Failed to get access token from provider"

            from app.services.auth_provider import ExternalUserInfo
            user_info_obj = await auth_provider.get_user_info(access_token)

            user_info = {
                "name": user_info_obj.name,
                "email": user_info_obj.email,
                "avatar_url": user_info_obj.avatar_url,
                "mobile": user_info_obj.mobile,
                "raw_data": user_info_obj.raw_data,
            }

            email_addr = user_info_obj.email
            tenant_id = None
            if email_addr:
                tenant = await self.detect_tenant_by_email(email_addr)
                tenant_id = tenant.id if tenant else None

            lookup_provider_user_id = (
                user_info_obj.provider_union_id or user_info_obj.provider_user_id
            )
            async with identity_dao.session() as db:
                existing_user = await sso_service.resolve_user_identity(
                    db,
                    lookup_provider_user_id,
                    provider_type,
                    tenant_id=tenant_id,
                    identity_data=user_info,
                )
                if existing_user:
                    return existing_user, False, None

                if user_info_obj.email:
                    existing_by_email = await sso_service.match_user_by_email(
                        db, user_info_obj.email, tenant_id=tenant_id
                    )
                    if existing_by_email:
                        await sso_service.link_identity(
                            db,
                            str(existing_by_email.id),
                            provider_type,
                            lookup_provider_user_id,
                            user_info,
                            tenant_id=(
                                str(existing_by_email.tenant_id)
                                if existing_by_email.tenant_id
                                else tenant_id
                            ),
                        )
                        return existing_by_email, False, None

            user, is_new = await self.handle_sso_registration(
                provider_type,
                lookup_provider_user_id,
                user_info,
            )

            await self.bind_org_member(user)
            return user, is_new, None

        except Exception:
            logger.exception("SSO registration failed for %s provider", provider_type)
            return None, False, f"SSO registration failed"

    # ── Tenant for registration ──────────────────────────────────────────────

    async def get_tenant_for_registration(
        self,
        email: str | None = None,
        invitation_code: str | None = None,
    ) -> tuple[Tenant | None, str]:
        """Determine tenant for new user registration."""
        if invitation_code:
            inv = await invitation_code_dao.get_active_by_code(invitation_code)
            if inv and inv.used_count < inv.max_uses:
                t = await tenant_dao.get(inv.tenant_id)
                if t and t.is_active:
                    return t, None
                return None, "Invitation code tenant is inactive"

        if email:
            tenant = await self.detect_tenant_by_email(email)
            if tenant:
                return tenant, None

        return None, None

    # ── OrgMember binding ────────────────────────────────────────────────────

    async def bind_org_member(self, user: User) -> None:
        """Find and bind OrgMember to User based on email/phone and tenant_id."""
        if not user.tenant_id:
            return

        member = await self._find_unbound_org_member_by_contact(user)
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

            async with org_member_dao.session() as db:
                await db.flush()

            from app.services.okr_agent_hook import hook_new_org_member
            async with org_member_dao.session() as db:
                await hook_new_org_member(db, member.id, user.tenant_id)

        await self.ensure_web_org_member(user)

    async def _find_unbound_org_member_by_contact(self, user: User):
        if user.email:
            member = await org_member_dao.find_unbound_by_email(user.email, user.tenant_id)
            if member:
                return member
        if user.primary_mobile:
            return await org_member_dao.find_unbound_by_phone(user.primary_mobile, user.tenant_id)
        return None

    async def ensure_web_org_member(self, user: User):
        """Ensure the user has a dedicated platform OrgMember record in their tenant."""
        if not user.tenant_id:
            return None

        from app.models.org import OrgMember

        web_provider = await self.ensure_identity_provider("web", user.tenant_id, name="Platform")
        if web_provider.name == "Web":
            web_provider.name = "Platform"

        # Look up existing OrgMember
        member = await org_member_dao.get_by_user_and_provider(
            user.id, user.tenant_id, web_provider.id
        )
        if not member and user.email:
            member = await org_member_dao.find_unbound_by_email_and_provider(
                user.email, user.tenant_id, web_provider.id
            )
        if not member and user.primary_mobile:
            member = await org_member_dao.find_unbound_by_phone_and_provider(
                user.primary_mobile, user.tenant_id, web_provider.id
            )

        created = False
        linked_existing = False
        async with org_member_dao.session() as db:
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
            async with org_member_dao.session() as db:
                await hook_new_org_member(db, member.id, user.tenant_id)

        return member

    async def sync_org_member_contact_from_user(
        self,
        user: User,
        *,
        sync_email: bool = False,
        sync_phone: bool = False,
    ) -> None:
        """Sync email/phone from User to linked OrgMember (user is source of truth)."""
        if not user.tenant_id or not (sync_email or sync_phone):
            return

        web_provider = await self.ensure_identity_provider("web", user.tenant_id, name="Platform")
        if web_provider.name == "Web":
            web_provider.name = "Platform"

        members = await org_member_dao.get_by_user_and_tenant_and_provider(
            user.id, user.tenant_id, web_provider.id
        )
        if not members:
            return

        async with org_member_dao.session() as db:
            for member in members:
                if sync_email and member.email != user.email:
                    member.email = user.email
                if sync_phone and member.phone != user.primary_mobile:
                    member.phone = user.primary_mobile
            await db.flush()


# Global registration service
registration_service = RegistrationService()
