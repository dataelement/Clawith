"""Channel user resolution service for messaging platforms.

This service provides unified user resolution for incoming messages from
external channels (DingTalk, WeCom, Feishu, etc.). It reuses the SSO service
and OrgMember-based identity management.
"""

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.agent import Agent
from app.models.identity import IdentityProvider
from app.models.org import OrgMember
from app.models.user import User
from app.services.sso_service import sso_service


class ChannelUserService:
    """Service for resolving channel users via OrgMember and SSO patterns."""

    def _get_channel_ids(
        self,
        channel_type: str,
        external_user_id: str,
        extra_info: dict[str, Any],
    ) -> tuple[str | None, str | None, str | None]:
        unionid = (extra_info.get("unionid") or extra_info.get("union_id") or "").strip() or None
        open_id = (extra_info.get("open_id") or "").strip() or None
        external_id = (extra_info.get("external_id") or external_user_id or "").strip() or None

        if channel_type == "feishu":
            open_id = open_id or external_user_id
            external_id = (extra_info.get("external_id") or "").strip() or None
        elif channel_type == "dingtalk":
            open_id = open_id or None
        elif channel_type == "wecom":
            unionid = None
            open_id = open_id or None

        return unionid, open_id, external_id

    async def resolve_channel_user(
        self,
        db: AsyncSession,
        agent: Agent,
        channel_type: str,
        external_user_id: str,
        extra_info: dict[str, Any] | None = None,
    ) -> User:
        """Resolve channel user identity, find or create platform User.

        Priority order:
        1. OrgMember already linked to User → return existing User
        2. OrgMember exists but not linked → create User and link
        3. User matched by email/mobile → return User and link OrgMember
        4. No match → create new User and OrgMember (lazy registration)

        Args:
            db: Database session
            agent: Agent receiving the message (for tenant_id)
            channel_type: "dingtalk" | "wecom" | "feishu"
            external_user_id: User ID from external platform (staff_id/userid/open_id)
            extra_info: Optional name/avatar/mobile/email from platform API

        Returns:
            Resolved User instance
        """
        tenant_id = agent.tenant_id
        extra_info = extra_info or {}

        # Step 1: Ensure IdentityProvider exists
        provider = await self._ensure_provider(db, channel_type, tenant_id)

        # Step 2: Try to find OrgMember by external identity
        org_member = await self._find_org_member(
            db, provider.id, channel_type, external_user_id
        )

        # Step 3: Resolve User from OrgMember or other means
        user = None

        if org_member and org_member.user_id:
            # Case 1: OrgMember already linked to User
            user = await db.get(User, org_member.user_id)
            if user:
                logger.debug(
                    f"[{channel_type}] Found user via linked OrgMember: {user.id}"
                )
                return user

        # Step 4: Try to find User by email/mobile from extra_info
        email = extra_info.get("email")
        mobile = extra_info.get("mobile")

        if not user and email:
            user = await sso_service.match_user_by_email(db, email, tenant_id)
            if user:
                logger.info(
                    f"[{channel_type}] Matched user by email: {user.id}"
                )

        if not user and mobile:
            user = await sso_service.match_user_by_mobile(db, mobile, tenant_id)
            if user:
                logger.info(
                    f"[{channel_type}] Matched user by mobile: {user.id}"
                )

        # If found User by email/mobile, link OrgMember if exists (only for org-sync channels)
        if user:
            if channel_type in ("feishu", "dingtalk", "wecom"):
                if org_member and not org_member.user_id:
                    # Existing shell OrgMember not yet linked → link it
                    org_member.user_id = user.id
                elif not org_member:
                    # No OrgMember found by external_id. Before creating a new shell,
                    # check if this user already has an OrgMember from org sync so
                    # we reuse it instead of creating a duplicate entry.
                    existing_member = await self._find_existing_org_member_for_user(
                        db, user.id, provider.id, tenant_id
                    )
                    if existing_member:
                        unionid, open_id, external_id = self._get_channel_ids(
                            channel_type, external_user_id, extra_info
                        )
                        if unionid and not existing_member.unionid:
                            existing_member.unionid = unionid
                        if open_id and not existing_member.open_id:
                            existing_member.open_id = open_id
                        if external_id and not existing_member.external_id:
                            existing_member.external_id = external_id
                        logger.info(
                            f"[{channel_type}] Reusing org-synced OrgMember {existing_member.id} "
                            f"for user {user.id} instead of creating a duplicate shell"
                        )
                    else:
                        # Truly no OrgMember for this user → create shell
                        await self._create_org_member_shell(
                            db, provider, channel_type, external_user_id, extra_info,
                            linked_user_id=user.id
                        )
            await db.flush()
            return user

        # Step 5: Create new User (lazy registration)
        user = await self._create_channel_user(
            db, channel_type, external_user_id, extra_info, tenant_id
        )

        # Step 6: Link or create OrgMember (only for channels with org sync)
        # Channels like Discord/Slack don't have OrgMember, skip this step
        if channel_type in ("feishu", "dingtalk", "wecom"):
            if org_member:
                org_member.user_id = user.id
            else:
                await self._create_org_member_shell(
                    db, provider, channel_type, external_user_id, extra_info,
                    linked_user_id=user.id
                )
            await db.flush()
        logger.info(
            f"[{channel_type}] Created new user: {user.id} for external_id: {external_user_id}"
        )

        return user

    async def _ensure_provider(
        self, db: AsyncSession, provider_type: str, tenant_id: uuid.UUID | None
    ) -> IdentityProvider:
        """Get or create IdentityProvider record."""
        query = select(IdentityProvider).where(
            IdentityProvider.provider_type == provider_type
        )
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)

        result = await db.execute(query)
        provider = result.scalar_one_or_none()

        if not provider:
            provider = IdentityProvider(
                provider_type=provider_type,
                name=provider_type.capitalize(),
                is_active=True,
                config={},
                tenant_id=tenant_id,
            )
            db.add(provider)
            await db.flush()

        return provider

    async def _find_org_member(
        self,
        db: AsyncSession,
        provider_id: uuid.UUID,
        channel_type: str,
        external_user_id: str,
    ) -> OrgMember | None:
        """Find OrgMember by external identity.

        For Feishu: try unionid first, then open_id, then external_id
        For DingTalk: try unionid first, then external_id
        For WeCom: try external_id (userid)

        Returns None if OrgMember not found or org sync is not enabled for this channel.
        """
        try:
            # Build OR conditions for matching
            conditions = [OrgMember.provider_id == provider_id, OrgMember.status == "active"]

            # Channel-specific matching priority
            if channel_type == "feishu":
                # Feishu: unionid is most stable, then open_id, then user_id
                conditions.append(
                    (OrgMember.unionid == external_user_id) |
                    (OrgMember.open_id == external_user_id) |
                    (OrgMember.external_id == external_user_id)
                )
            elif channel_type == "dingtalk":
                # DingTalk: unionid is stable across apps, then external_id
                conditions.append(
                    (OrgMember.unionid == external_user_id) |
                    (OrgMember.external_id == external_user_id)
                )
            elif channel_type == "wecom":
                # WeCom: external_id (userid) is the primary identifier
                conditions.append(OrgMember.external_id == external_user_id)
            else:
                # Generic fallback (discord, slack, etc. - no org sync)
                # These channels don't have OrgMember, return None immediately
                return None

            query = select(OrgMember).where(*conditions)
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            # OrgMember table may not exist or org sync not enabled
            logger.debug(f"[{channel_type}] OrgMember lookup failed: {e}")
            return None

    async def _create_org_member_shell(
        self,
        db: AsyncSession,
        provider: IdentityProvider,
        channel_type: str,
        external_user_id: str,
        extra_info: dict[str, Any],
        linked_user_id: uuid.UUID | None = None,
    ) -> OrgMember:
        """Create a shell OrgMember record for this identity."""
        name = extra_info.get("name") or f"{channel_type.capitalize()} User {external_user_id[:8]}"
        unionid, open_id, external_id = self._get_channel_ids(channel_type, external_user_id, extra_info)

        member = OrgMember(
            name=name,
            email=extra_info.get("email"),
            provider_id=provider.id,
            user_id=linked_user_id,
            tenant_id=provider.tenant_id,
            external_id=external_id,
            unionid=unionid,
            open_id=open_id,
            avatar_url=extra_info.get("avatar_url"),
            phone=extra_info.get("mobile"),
            title=extra_info.get("title", ""),
            status="active",
        )
        db.add(member)
        await db.flush()
        return member

    async def _find_existing_org_member_for_user(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        provider_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
    ) -> OrgMember | None:
        """Find an existing OrgMember already linked to the given platform User.

        Used before creating a shell record to avoid duplicate OrgMember entries
        when an org-sync-sourced record already exists for the same user.
        """
        query = select(OrgMember).where(
            OrgMember.user_id == user_id,
            OrgMember.provider_id == provider_id,
            OrgMember.status == "active",
        )
        if tenant_id:
            query = query.where(OrgMember.tenant_id == tenant_id)
        result = await db.execute(query.limit(1))
        return result.scalar_one_or_none()

    async def _create_channel_user(
        self,
        db: AsyncSession,
        channel_type: str,
        external_user_id: str,
        extra_info: dict[str, Any],
        tenant_id: uuid.UUID | None,
    ) -> User:
        """Create a new Identity + User for channel identity (lazy registration).

        Creates a global Identity first, then a tenant-scoped User linked to it.
        This ensures compatibility with the Phase 2 user model where username,
        email, and password_hash live on the Identity table.
        """
        from app.models.user import Identity

        # Generate username and email
        email = extra_info.get("email")
        name = extra_info.get("name") or f"{channel_type.capitalize()} {external_user_id[:8]}"

        if email:
            username = email.split("@")[0]
        else:
            username = f"{channel_type}_{external_user_id[:12]}"

        # Ensure unique username within tenant
        from app.models.user import User, Identity
        query = (
            select(User)
            .join(User.identity)
            .where(Identity.username == username)
        )
        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)

        existing = await db.execute(query)
        if existing.scalar_one_or_none():
            username = f"{username}_{external_user_id[:6]}"

        email = email or f"{username}@{channel_type}.local"

        # Step 1: Find or create global Identity using unified registration service
        from app.services.registration_service import registration_service
        identity = await registration_service.find_or_create_identity(
            db,
            email=email,
            phone=extra_info.get("mobile"),
            username=username,
            password=uuid.uuid4().hex,
        )


        # Step 2: Create tenant-scoped User linked to Identity
        user = User(
            identity_id=identity.id,
            display_name=name,
            avatar_url=extra_info.get("avatar_url"),
            role="member",
            registration_source=channel_type,
            tenant_id=tenant_id,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        return user


# Global service instance
channel_user_service = ChannelUserService()


async def get_platform_user_by_org_member(
    db: AsyncSession,
    org_member: OrgMember,
    agent_tenant_id: uuid.UUID | None = None,
) -> User:
    """Get or create platform User from an existing OrgMember.

    This is used by agent_tools.py when sending proactive messages:
    - OrgMember already exists (from AgentRelationship)
    - But user_id may be NULL (not yet linked to platform User)
    - We need to get or create the User and link it

    Args:
        db: Database session
        org_member: Existing OrgMember instance
        agent_tenant_id: Optional tenant ID for scoping

    Returns:
        Linked/created User instance
    """
    # Case 1: OrgMember already linked to User
    if org_member.user_id:
        user = await db.get(User, org_member.user_id)
        if user:
            return user

    # Case 2: Try to find User by email/mobile from OrgMember
    user = None
    if org_member.email:
        user = await sso_service.match_user_by_email(db, org_member.email, agent_tenant_id)
    if not user and org_member.phone:
        user = await sso_service.match_user_by_mobile(db, org_member.phone, agent_tenant_id)

    if user:
        # Link existing User to OrgMember
        org_member.user_id = user.id
        await db.flush()
        return user

    # Case 3: Create new User and link to OrgMember
    # Determine channel type from provider
    from app.models.identity import IdentityProvider
    provider = await db.get(IdentityProvider, org_member.provider_id)
    channel_type = provider.provider_type if provider else "unknown"

    # Generate username from OrgMember info
    email = org_member.email
    name = org_member.name or f"{channel_type.capitalize()} User {org_member.external_id[:8]}"

    if email:
        username = email.split("@")[0]
    elif org_member.external_id:
        username = f"{channel_type}_{org_member.external_id[:12]}"
    else:
        username = f"{channel_type}_{org_member.id.hex[:12]}"

    # Ensure unique username within tenant
    from app.models.user import User, Identity
    query = (
        select(User)
        .join(User.identity)
        .where(Identity.username == username)
    )
    if agent_tenant_id:
        query = query.where(User.tenant_id == agent_tenant_id)

    existing = await db.execute(query)
    if existing.scalar_one_or_none():
        username = f"{username}_{org_member.external_id[:6] if org_member.external_id else org_member.id.hex[:6]}"

    email = email or f"{username}@{channel_type}.local"

    # Step 3: Create new User and link to OrgMember
    from app.services.registration_service import registration_service
    # Use unified find_or_create_identity with dual lookup (email/phone)
    identity = await registration_service.find_or_create_identity(
        db,
        email=email,
        phone=org_member.phone,
        username=username,
        password=uuid.uuid4().hex,
    )


    user = User(
        identity_id=identity.id,
        display_name=name,
        avatar_url=org_member.avatar_url,
        role="member",
        registration_source=channel_type,
        tenant_id=agent_tenant_id,
        is_active=True,
    )

    db.add(user)
    await db.flush()

    # Link OrgMember to new User
    org_member.user_id = user.id
    await db.flush()

    logger.info(f"[channel_user_service] Created User {user.id} for OrgMember {org_member.id} ({name})")
    return user
