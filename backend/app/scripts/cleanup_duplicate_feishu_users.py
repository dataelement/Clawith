"""Migration script: Backfill feishu_user_id and clean up duplicate users.

This script:
1. Uses the org sync App credentials to resolve user_id for all users that only have open_id
2. Merges duplicate users (same display_name + feishu identity but different records)
3. Updates chat session conv_ids from feishu_p2p_{open_id} to feishu_p2p_{user_id}

Usage:
  Docker:  docker exec clawith-backend-1 python3 -m app.scripts.cleanup_duplicate_feishu_users
  Source:  cd backend && python3 -m app.scripts.cleanup_duplicate_feishu_users
"""

import asyncio
from loguru import logger


async def main():
    # Import ALL models so SQLAlchemy can resolve all FK relationships
    from app.models import (  # noqa: F401
        activity_log, agent, audit, channel_config, chat_session,
        gateway_message, invitation_code, llm, notification, org,
        participant, plaza, schedule, skill, system_settings, task,
        tenant, tenant_setting, tool, trigger, user,
    )
    from app.database import async_session
    from app.models.user import User
    from app.models.org import OrgMember
    from app.services.auth_registry import auth_provider_registry
    from app.models.chat_session import ChatSession
    from app.models.audit import ChatMessage
    from sqlalchemy import select, update, func
    import httpx

    async with async_session() as db:
        # ── Step 0: Load org sync app credentials ──
        provider = await auth_provider_registry.get_provider(db, "feishu")
        if not provider:
            logger.warning("No feishu identity provider configured. Cannot resolve user_ids. Skipping backfill.")
            logger.info("You can still run Sync Now from the UI after configuring feishu identity provider.")
            return

        conf = provider.config or {}
        app_id = conf.get("app_id") or conf.get("client_id")
        app_secret = conf.get("app_secret") or conf.get("client_secret")
        if not app_id or not app_secret:
            logger.warning("Feishu identity provider missing app_id/app_secret. Skipping backfill.")
            return

        # Get app token
        async with httpx.AsyncClient() as client:
            tok_resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            app_token = tok_resp.json().get("app_access_token", "")

        if not app_token:
            logger.error("Failed to get app token. Check org sync App credentials.")
            return

        # ── Step 1: Backfill user_id for Users ──
        logger.info("=== Step 1: Backfill feishu_user_id for Users ===")
        logger.info("Skipped: User.open_id/union_id removed; use OrgMember backfill instead.")

        # ── Step 2: Backfill user_id for OrgMembers ──
        logger.info("=== Step 2: Backfill feishu_user_id for OrgMembers ===")
        r = await db.execute(
            select(OrgMember).where(
                OrgMember.open_id.isnot(None),
                (OrgMember.external_id.is_(None)) | (OrgMember.external_id == ""),
            )
        )
        members_to_fill = r.scalars().all()
        logger.info(f"Found {len(members_to_fill)} org members needing user_id backfill")

        member_filled = 0
        for member in members_to_fill:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://open.feishu.cn/open-apis/contact/v3/users/{member.open_id}",
                        params={"user_id_type": "open_id"},
                        headers={"Authorization": f"Bearer {app_token}"},
                    )
                    data = resp.json()
                    if data.get("code") == 0:
                        user_id = data.get("data", {}).get("user", {}).get("user_id", "")
                        if user_id:
                            member.external_id = user_id
                            member_filled += 1
                    else:
                        logger.warning(f"  Cannot resolve OrgMember {member.name} (code={data.get('code')})")
            except Exception as e:
                logger.error(f"  Error resolving OrgMember {member.name}: {e}")

        await db.commit()
        logger.info(f"Backfilled user_id for {member_filled}/{len(members_to_fill)} org members")

        # ── Step 2.5: Merge duplicate OrgMembers ──
        logger.info("=== Step 2.5: Merge duplicate OrgMembers ===")
        from app.models.org import AgentRelationship

        r = await db.execute(
            select(OrgMember.name, OrgMember.tenant_id, func.count(OrgMember.id).label("cnt"))
            .where(OrgMember.name.isnot(None), OrgMember.name != "")
            .group_by(OrgMember.name, OrgMember.tenant_id)
            .having(func.count(OrgMember.id) > 1)
        )
        om_dup_groups = r.all()
        om_merge_count = 0
        logger.info(f"Found {len(om_dup_groups)} groups of duplicate OrgMembers")

        for name, tid, cnt in om_dup_groups:
            q = select(OrgMember).where(OrgMember.name == name)
            if tid:
                q = q.where(OrgMember.tenant_id == tid)
            else:
                q = q.where(OrgMember.tenant_id.is_(None))
            q = q.order_by(OrgMember.synced_at.desc())  # Keep the most recently synced
            r2 = await db.execute(q)
            dups = r2.scalars().all()
            if len(dups) <= 1:
                continue

            # Pick best: prefer has user_id > has open_id > most recent
            def om_score(m):
                s = 0
                if m.external_id:
                    s += 10
                if m.open_id:
                    s += 1
                return s

            dups_sorted = sorted(dups, key=lambda m: (-om_score(m), m.synced_at))
            primary = dups_sorted[0]
            to_merge = dups_sorted[1:]

            logger.info(f"  Merging {cnt} OrgMembers named '{name}', keeping id={primary.id}")

            for dup in to_merge:
                # Migrate agent_relationships FK
                await db.execute(
                    update(AgentRelationship)
                    .where(AgentRelationship.member_id == dup.id)
                    .values(member_id=primary.id)
                )
                # Transfer missing identity fields
                if dup.external_id and not primary.external_id:
                    primary.external_id = dup.external_id
                if dup.email and primary.email != dup.email and dup.email:
                    if not primary.email:
                        primary.email = dup.email
                # Clear unique field before delete
                dup.open_id = None
                await db.flush()
                await db.delete(dup)
                om_merge_count += 1

            try:
                await db.commit()
            except Exception as e:
                logger.error(f"  Failed to commit OrgMember merge for '{name}': {e}")
                await db.rollback()

        logger.info(f"Merged {om_merge_count} duplicate OrgMembers")

        # ── Step 3: Merge duplicate users ──
        logger.info("=== Step 3: Merge duplicate users ===")

        # Find duplicate display_names within the same tenant
        # These are likely the same person created multiple times from different apps
        from sqlalchemy import or_, and_, cast, String as SAString
        r = await db.execute(
            select(User.display_name, User.tenant_id, func.count(User.id).label("cnt"))
            .where(User.display_name.isnot(None), User.display_name != "")
            .group_by(User.display_name, User.tenant_id)
            .having(func.count(User.id) > 1)
        )
        dup_groups = r.all()
        merge_count = 0
        logger.info(f"Found {len(dup_groups)} groups of duplicate display_names")

        for name, tid, cnt in dup_groups:
            q = select(User).where(User.display_name == name)
            if tid:
                q = q.where(User.tenant_id == tid)
            else:
                q = q.where(User.tenant_id.is_(None))
            q = q.order_by(User.created_at.asc())
            r2 = await db.execute(q)
            dups = r2.scalars().all()

            if len(dups) <= 1:
                continue

            # Pick the best record as primary:
            # Priority: has real email > has feishu_user_id > oldest
            def score(u):
                s = 0
                if u.email and "@" in u.email and not u.email.endswith("@feishu.local"):
                    s += 100  # Real email = likely registered user
                if u.feishu_user_id:
                    s += 10
                return s

            dups_sorted = sorted(dups, key=lambda u: (-score(u), u.created_at))
            primary = dups_sorted[0]
            to_merge = dups_sorted[1:]

            logger.info(f"  Merging {cnt} users named '{name}', keeping {primary.username} (email={primary.email})")

            for dup in to_merge:
                # Migrate chat messages
                await db.execute(
                    update(ChatMessage)
                    .where(ChatMessage.user_id == dup.id)
                    .values(user_id=primary.id)
                )
                # Migrate chat sessions
                await db.execute(
                    update(ChatSession)
                    .where(ChatSession.user_id == dup.id)
                    .values(user_id=primary.id)
                )
                # Transfer missing identity fields to primary
                if dup.email and "@" in dup.email and not dup.email.endswith("@feishu.local"):
                    if not primary.email or primary.email.endswith("@feishu.local"):
                        primary.email = dup.email
                if dup.feishu_user_id and not primary.feishu_user_id:
                    primary.feishu_user_id = dup.feishu_user_id
                # Clear identity fields on duplicate before delete to avoid constraint violations
                dup.email = f"deleted_{dup.id}@deleted.local"
                dup.username = f"deleted_{dup.id}"
                await db.flush()
                # Now safe to delete
                await db.delete(dup)
                merge_count += 1
                logger.info(f"    Merged {dup.display_name} ({dup.id}) into {primary.username}")

            # Commit after each group to isolate errors
            try:
                await db.commit()
            except Exception as e:
                logger.error(f"  Failed to commit merge for '{name}': {e}")
                await db.rollback()

        logger.info(f"Merged {merge_count} duplicate users")

        # ── Step 4: Update conv_ids ──
        logger.info("=== Step 4: Update session conv_ids ===")

        # Find sessions with old-style feishu_p2p_{open_id} conv_ids
        r = await db.execute(
            select(ChatSession).where(ChatSession.external_conv_id.like("feishu_p2p_%"))
        )
        sessions = r.scalars().all()
        updated_sessions = 0

        for sess in sessions:
            old_conv = sess.external_conv_id
            # Extract the ID part
            old_id = old_conv.replace("feishu_p2p_", "")

            # Check if the old_id looks like an open_id (starts with "ou_")
            if old_id.startswith("ou_"):
                # Look up the user to find their user_id
                om_r = await db.execute(
                    select(OrgMember).where(OrgMember.open_id == old_id)
                )
                om = om_r.scalar_one_or_none()
                if om and om.external_id:
                    new_conv = f"feishu_p2p_{om.external_id}"
                    sess.external_conv_id = new_conv
                    updated_sessions += 1
                    logger.info(f"  Updated session conv_id: {old_conv} -> {new_conv}")

        await db.commit()
        logger.info(f"Updated {updated_sessions}/{len(sessions)} session conv_ids")

    logger.info("=== Migration complete ===")


if __name__ == "__main__":
    asyncio.run(main())
