"""Channel user resolution service for messaging platforms.

This service provides unified user resolution for incoming messages from
external channels (DingTalk, WeCom, Feishu, etc.). It reuses the SSO service
and OrgMember-based identity management.
"""

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import or_, select
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
        extra_ids: list[str] | None = None,
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
            extra_ids: Additional candidate identifiers (e.g. real unionid discovered
                via user/get) OR-matched against OrgMember.unionid/external_id.

        Returns:
            Resolved User instance
        """
        tenant_id = agent.tenant_id
        extra_info = extra_info or {}

        # Step 1: Ensure IdentityProvider exists
        provider = await self._ensure_provider(db, channel_type, tenant_id)

        # Step 2: Try to find OrgMember by all candidate identifiers
        candidate_ids: list[str] = [external_user_id]
        for cid in (extra_ids or []):
            if cid and cid not in candidate_ids:
                candidate_ids.append(cid)
        org_member = await self._find_org_member(
            db, provider.id, channel_type, candidate_ids
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
                try:
                    await self._enrich_user_from_extra_info(db, user, extra_info)
                except Exception:
                    logger.exception(
                        f"[{channel_type}] enrichment failed for user {user.id}; "
                        f"continuing without enrichment"
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

        # If found User by email/mobile, enrich and link OrgMember
        if user:
            try:
                await self._enrich_user_from_extra_info(db, user, extra_info)
            except Exception:
                logger.exception(
                    f"[{channel_type}] enrichment failed for user {user.id}; "
                    f"continuing without enrichment"
                )
            if channel_type in ("feishu", "dingtalk", "wecom"):
                if org_member and not org_member.user_id:
                    # Existing shell OrgMember not yet linked → link it + backfill ids
                    org_member.user_id = user.id
                    self._backfill_org_member_ids(
                        org_member, channel_type, external_user_id, extra_info
                    )
                elif not org_member:
                    existing_member = await self._find_existing_org_member_for_user(
                        db, user.id, provider.id, tenant_id
                    )
                    if existing_member:
                        # Reuse the org-synced record: back-fill channel identifiers
                        # so future direct lookups hit without another user/get call.
                        self._backfill_org_member_ids(
                            existing_member, channel_type, external_user_id, extra_info
                        )
                        logger.info(
                            f"[{channel_type}] Reusing org-synced OrgMember "
                            f"{existing_member.id} for user {user.id}; "
                            f"back-filled channel identifiers"
                        )
                    else:
                        await self._create_org_member_shell(
                            db, provider, channel_type, external_user_id, extra_info,
                            linked_user_id=user.id,
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
                self._backfill_org_member_ids(
                    org_member, channel_type, external_user_id, extra_info
                )
            else:
                await self._create_org_member_shell(
                    db, provider, channel_type, external_user_id, extra_info,
                    linked_user_id=user.id,
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
        candidate_ids: list[str],
    ) -> OrgMember | None:
        """Find OrgMember by a list of candidate external identifiers.

        所有候选 ID 走 OR 匹配, 适配钉钉同时拥有 staff_id 与 unionid 的场景。
        """
        if not candidate_ids:
            return None
        try:
            base = [OrgMember.provider_id == provider_id, OrgMember.status == "active"]

            if channel_type == "feishu":
                id_match = or_(
                    OrgMember.unionid.in_(candidate_ids),
                    OrgMember.open_id.in_(candidate_ids),
                    OrgMember.external_id.in_(candidate_ids),
                )
            elif channel_type == "dingtalk":
                id_match = or_(
                    OrgMember.unionid.in_(candidate_ids),
                    OrgMember.external_id.in_(candidate_ids),
                )
            elif channel_type == "wecom":
                id_match = OrgMember.external_id.in_(candidate_ids)
            else:
                return None

            query = select(OrgMember).where(*base, id_match)
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
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

    def _backfill_org_member_ids(
        self,
        member: OrgMember,
        channel_type: str,
        external_user_id: str,
        extra_info: dict[str, Any],
    ) -> None:
        """回填 channel 特定的 identifier 到现有 OrgMember(只填空字段)。

        幂等: 重复调用不覆盖非空值。不写库, 依赖外层 flush。
        """
        unionid_from_api = extra_info.get("unionid")

        if channel_type == "dingtalk":
            if not member.external_id and external_user_id:
                member.external_id = external_user_id
            if not member.unionid and unionid_from_api:
                member.unionid = unionid_from_api

        elif channel_type == "feishu":
            if external_user_id.startswith("on_"):
                if not member.unionid:
                    member.unionid = external_user_id
            elif external_user_id.startswith("ou_"):
                if not member.open_id:
                    member.open_id = external_user_id
            if not member.external_id and external_user_id:
                member.external_id = external_user_id
            if not member.unionid and unionid_from_api:
                member.unionid = unionid_from_api

        elif channel_type == "wecom":
            if not member.external_id and external_user_id:
                member.external_id = external_user_id

    async def _enrich_user_from_extra_info(
        self,
        db: AsyncSession,
        user: User,
        extra_info: dict[str, Any],
    ) -> None:
        """Enrich existing user with mobile/email/name from channel extra_info.

        Only fills in fields that are currently empty on the user AND not
        already claimed by another Identity (Identity.phone/email are globally
        unique — writing a value that exists elsewhere would raise
        IntegrityError and break the caller). On conflict, the field is
        silently skipped (logged at warning level).
        """
        from app.models.user import Identity

        updated = False
        name = extra_info.get("name")
        mobile = extra_info.get("mobile")
        email = extra_info.get("email")
        avatar = extra_info.get("avatar_url")

        if name and not user.display_name:
            user.display_name = name
            updated = True
        if avatar and not user.avatar_url:
            user.avatar_url = avatar
            updated = True

        # Enrich Identity-level fields (phone, email) if available.
        # Pre-check for conflicts on globally unique fields to avoid
        # IntegrityError from collision with another Identity.
        if user.identity_id and (mobile or email):
            identity = await db.get(Identity, user.identity_id)
            if identity:
                if mobile and not identity.phone:
                    if await self._identity_field_in_use(
                        db, Identity.phone, mobile, identity.id
                    ):
                        logger.warning(
                            f"[enrich] phone={mobile} already claimed by another "
                            f"identity; skipping phone backfill for identity {identity.id}"
                        )
                    else:
                        identity.phone = mobile
                        updated = True
                if email and not identity.email:
                    if await self._identity_field_in_use(
                        db, Identity.email, email, identity.id
                    ):
                        logger.warning(
                            f"[enrich] email={email} already claimed by another "
                            f"identity; skipping email backfill for identity {identity.id}"
                        )
                    else:
                        identity.email = email
                        updated = True

        if updated:
            await db.flush()

    async def _identity_field_in_use(
        self,
        db: AsyncSession,
        column,
        value: str,
        exclude_identity_id: uuid.UUID,
    ) -> bool:
        """Check whether any OTHER Identity already holds the given value on column.

        Used to pre-empt IntegrityError on Identity.phone/email (globally unique).
        """
        from app.models.user import Identity

        stmt = select(Identity.id).where(
            column == value, Identity.id != exclude_identity_id
        ).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is not None

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
