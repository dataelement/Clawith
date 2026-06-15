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
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.identity import IdentityProvider
from app.models.org import OrgMember
from app.models.user import Identity, User
from app.services.sso_service import sso_service


class ChannelUserResolutionError(ValueError):
    """Raised when a channel message cannot be safely attributed to a user."""


class ChannelUserService:
    """Service for resolving channel users via OrgMember and SSO patterns."""

    CHANNEL_TYPE_ALIASES = {
        "microsoft_teams": "teams",
    }

    def _normalize_channel_type(self, channel_type: str) -> str:
        raw = (channel_type or "").strip().lower()
        return self.CHANNEL_TYPE_ALIASES.get(raw, raw)

    def _legacy_provider_types_for_channel(self, channel_type: str) -> list[str]:
        normalized = self._normalize_channel_type(channel_type)
        legacy = [normalized]
        if normalized == "teams":
            legacy.append("microsoft_teams")
        elif normalized == "microsoft_teams":
            legacy.append("teams")
        return legacy

    def _get_channel_ids(
        self,
        channel_type: str,
        external_user_id: str | None,
        extra_info: dict[str, Any],
    ) -> tuple[str | None, str | None, str | None]:
        normalized_channel = self._normalize_channel_type(channel_type)
        unionid = (extra_info.get("unionid") or extra_info.get("union_id") or "").strip() or None
        open_id = (extra_info.get("open_id") or "").strip() or None
        external_id = (extra_info.get("external_id") or external_user_id or "").strip() or None

        if normalized_channel == "feishu":
            # Feishu external_id must remain tenant-stable user_id only.
            # Never backfill it from open_id.
            external_id = (extra_info.get("external_id") or "").strip() or None
        elif normalized_channel == "dingtalk":
            open_id = open_id or None
        elif normalized_channel == "wecom":
            unionid = None
            open_id = open_id or None
        else:
            unionid = None
            open_id = None

        return unionid, open_id, external_id

    async def resolve_channel_user(
        self,
        db: AsyncSession,
        agent: Agent,
        channel_type: str,
        external_user_id: str | None,
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
            channel_type: "dingtalk" | "wecom" | "wechat" | "feishu"
            external_user_id: User ID from external platform. For Feishu this must be user_id, not open_id.
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
            db, provider.id, channel_type, external_user_id, extra_info
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

        should_persist_member = True

        # If found User by email/mobile, link OrgMember if exists
        if user:
            if should_persist_member:
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

        unionid, open_id, external_id = self._get_channel_ids(
            channel_type, external_user_id, extra_info
        )

        if channel_type == "feishu" and not org_member and not (unionid or external_id):
            raise ChannelUserResolutionError(
                "Feishu sender could not be resolved to a stable user_id/union_id; "
                "refusing to lazily create a duplicate user from open_id only."
            )

        # Step 5: Create new User (lazy registration)
        user = await self._create_channel_user(
            db, channel_type, external_user_id, extra_info, tenant_id
        )

        # Step 6: Link or create OrgMember
        if should_persist_member:
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
        canonical_type = self._normalize_channel_type(provider_type)

        query = select(IdentityProvider).where(
            IdentityProvider.provider_type == canonical_type
        )
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)

        result = await db.execute(query)
        provider = result.scalar_one_or_none()
        if provider:
            return provider

        for legacy_type in self._legacy_provider_types_for_channel(provider_type):
            if legacy_type == canonical_type:
                continue
            legacy_query = select(IdentityProvider).where(
                IdentityProvider.provider_type == legacy_type
            )
            if tenant_id:
                legacy_query = legacy_query.where(IdentityProvider.tenant_id == tenant_id)
            legacy_result = await db.execute(legacy_query)
            legacy_provider = legacy_result.scalar_one_or_none()
            if legacy_provider:
                return legacy_provider

        provider = IdentityProvider(
            provider_type=canonical_type,
            name=canonical_type.capitalize(),
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
        external_user_id: str | None,
        extra_info: dict[str, Any] | None = None,
    ) -> OrgMember | None:
        """Find OrgMember by external identity.

        For Feishu: try unionid first, then open_id, then external_id
        For DingTalk: try unionid first, then external_id
        For WeCom: try external_id (userid)
        For WeChat: try external_id (from_user_id)

        Returns None if OrgMember not found or org sync is not enabled for this channel.
        """
        try:
            extra_info = extra_info or {}
            unionid, open_id, external_id = self._get_channel_ids(
                channel_type, external_user_id, extra_info
            )

            # Build OR conditions for matching
            conditions = [OrgMember.provider_id == provider_id, OrgMember.status == "active"]

            # Channel-specific matching priority
            normalized_channel = self._normalize_channel_type(channel_type)
            if normalized_channel == "feishu":
                # Feishu identifiers have distinct semantics:
                # unionid/open_id come from extra_info; external_id is user_id only.
                lookup_conditions = []
                if unionid:
                    lookup_conditions.append(OrgMember.unionid == unionid)
                if open_id:
                    lookup_conditions.append(OrgMember.open_id == open_id)
                if external_id:
                    lookup_conditions.append(OrgMember.external_id == external_id)
                if not lookup_conditions:
                    return None
                conditions.append(lookup_conditions[0])
                for cond in lookup_conditions[1:]:
                    conditions[-1] = conditions[-1] | cond
            elif normalized_channel == "dingtalk":
                # DingTalk: unionid is stable across apps, then external_id
                lookup_conditions = []
                if unionid:
                    lookup_conditions.append(OrgMember.unionid == unionid)
                if external_id:
                    lookup_conditions.append(OrgMember.external_id == external_id)
                if not lookup_conditions:
                    return None
                conditions.append(lookup_conditions[0])
                for cond in lookup_conditions[1:]:
                    conditions[-1] = conditions[-1] | cond
            elif normalized_channel == "wecom":
                # WeCom: external_id (userid) is the primary identifier
                if not external_id:
                    return None
                conditions.append(OrgMember.external_id == external_id)
            else:
                # Generic channels: provider is already channel-scoped, so external_id
                # can be used directly without namespacing.
                if not external_id:
                    return None
                conditions.append(OrgMember.external_id == external_id)

            # Use limit(1) + prioritize records that are already linked to a User.
            # scalar_one_or_none() would raise MultipleResultsFound when duplicate
            # OrgMember shells exist for the same external_user_id — which was the
            # root cause of continuous new-user creation on every Feishu message.
            query = (
                select(OrgMember)
                .where(*conditions)
                .order_by(
                    # Prefer rows already linked to a platform User
                    OrgMember.user_id.isnot(None).desc(),
                    # Among equals, pick the oldest (most likely the "canonical" one)
                    OrgMember.synced_at.asc(),
                )
                .limit(1)
            )
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
        external_user_id: str | None,
        extra_info: dict[str, Any],
        linked_user_id: uuid.UUID | None = None,
    ) -> OrgMember:
        """Create a shell OrgMember record for this identity."""
        identity_seed = (
            external_user_id
            or (extra_info.get("open_id") or "").strip()
            or uuid.uuid4().hex
        )
        name = extra_info.get("name") or f"{channel_type.capitalize()} User {identity_seed[:8]}"
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
        external_user_id: str | None,
        extra_info: dict[str, Any],
        tenant_id: uuid.UUID | None,
    ) -> User:
        """Create a new Identity + User for channel identity (lazy registration).

        Creates a global Identity first, then a tenant-scoped User linked to it.
        Both objects are added to the SAME ``db`` session so the FK constraint
        (users.identity_id → identities.id) is satisfied within one transaction.

        The previous implementation called ``registration_service.find_or_create_identity``
        which delegates to ``identity_dao.create_identity``.  That DAO method uses its own
        ``async with self.session()`` context.  When ``_session_ctx`` is not set (all
        background channel handlers use raw ``async_session()`` directly, not FastAPI
        ``Depends(get_db)``), the DAO opens a **separate** session, flushes the Identity
        there, and exits — but SQLAlchemy does NOT auto-commit on session close, so the
        Identity is **rolled back**.  The User INSERT that follows on the outer ``db``
        session then violates the FK constraint.
        """
        import re as _re

        email = extra_info.get("email")
        mobile = extra_info.get("mobile")
        identity_seed = (
            external_user_id
            or (extra_info.get("open_id") or "").strip()
            or uuid.uuid4().hex
        )
        name = extra_info.get("name") or f"{channel_type.capitalize()} {identity_seed[:8]}"

        if email:
            username = email.split("@")[0]
        else:
            username = f"{channel_type}_{identity_seed[:12]}"

        # Ensure unique username within tenant
        query = (
            select(User)
            .join(User.identity)
            .where(Identity.username == username)
        )
        if tenant_id:
            query = query.where(User.tenant_id == tenant_id)

        existing = await db.execute(query)
        if existing.scalar_one_or_none():
            username = f"{username}_{identity_seed[:6]}"

        email = email or f"{username}@{channel_type}.local"

        # ── Step 1: Find or create Identity on the SAME session ──────────────
        # First try to find an existing Identity by email / phone so we don't
        # create duplicate identities for the same person across channels.
        from sqlalchemy import or_
        identity: Identity | None = None

        lookup_conditions = [Identity.email == email]
        if mobile:
            normalized_mobile = _re.sub(r"[\s\-\+]", "", mobile)
            lookup_conditions.append(Identity.phone == normalized_mobile)

        id_result = await db.execute(
            select(Identity).where(or_(*lookup_conditions)).limit(1)
        )
        identity = id_result.scalar_one_or_none()

        if not identity:
            normalized_phone = _re.sub(r"[\s\-\+]", "", mobile) if mobile else None
            identity = Identity(
                email=email,
                phone=normalized_phone,
                username=username,
                password_hash=None,
                is_platform_admin=False,
                email_verified=True,  # auto-verify channel users
            )
            db.add(identity)
            await db.flush()  # assigns identity.id within this transaction

        # ── Step 2: Create tenant-scoped User linked to Identity ─────────────
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
        query = (
            select(User)
            .where(User.id == org_member.user_id)
            .options(selectinload(User.identity))
        )
        if agent_tenant_id:
            query = query.where(User.tenant_id == agent_tenant_id)
        user_res = await db.execute(query)
        user = user_res.scalar_one_or_none()
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
        # Eagerly load/refresh User.identity before returning
        user_res = await db.execute(
            select(User).where(User.id == user.id).options(selectinload(User.identity))
        )
        return user_res.scalar_one()

    # Case 3: Create new User and link to OrgMember
    # Determine channel type from provider
    from app.models.identity import IdentityProvider
    provider = await db.get(IdentityProvider, org_member.provider_id)
    channel_type = provider.provider_type if provider else "unknown"
    external_seed = org_member.external_id

    # Generate username from OrgMember info
    email = org_member.email
    seed_for_name = external_seed or org_member.id.hex
    name = org_member.name or f"{channel_type.capitalize()} User {seed_for_name[:8]}"

    if email:
        username = email.split("@")[0]
    elif external_seed:
        username = f"{channel_type}_{external_seed[:12]}"
    else:
        username = f"{channel_type}_{org_member.id.hex[:12]}"

    # Ensure unique username within tenant
    query = (
        select(User)
        .join(User.identity)
        .where(Identity.username == username)
    )
    if agent_tenant_id:
        query = query.where(User.tenant_id == agent_tenant_id)

    existing = await db.execute(query)
    if existing.scalar_one_or_none():
        username = f"{username}_{external_seed[:6] if external_seed else org_member.id.hex[:6]}"

    email = email or f"{username}@{channel_type}.local"

    # Step 3: Create new Identity on the SAME session, then User + link OrgMember.
    # Using registration_service.find_or_create_identity would route through
    # identity_dao which opens its own session (no _session_ctx here), causing
    # the Identity to be rolled back before the User FK reference is resolved.
    from sqlalchemy import or_
    import re as _re_pu

    identity: Identity | None = None
    lookup_conditions = [Identity.email == email]
    if org_member.phone:
        normalized_ph = _re_pu.sub(r"[\s\-\+]", "", org_member.phone)
        lookup_conditions.append(Identity.phone == normalized_ph)

    id_result = await db.execute(
        select(Identity).where(or_(*lookup_conditions)).limit(1)
    )
    identity = id_result.scalar_one_or_none()

    if not identity:
        normalized_phone = _re_pu.sub(r"[\s\-\+]", "", org_member.phone) if org_member.phone else None
        identity = Identity(
            email=email,
            phone=normalized_phone,
            username=username,
            password_hash=None,
            is_platform_admin=False,
            email_verified=True,
        )
        db.add(identity)
        await db.flush()

    user = User(
        identity=identity,
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
    
    # Eagerly load/refresh User.identity before returning
    user_res = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.identity))
    )
    return user_res.scalar_one()
