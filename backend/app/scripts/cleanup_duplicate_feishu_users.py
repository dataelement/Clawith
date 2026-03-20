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
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


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
    from app.models.system_settings import SystemSetting
    from app.models.chat_session import ChatSession
    from app.models.audit import ChatMessage
    from sqlalchemy import select, update, func
    import httpx

    async with async_session() as db:
        # ── Step 0: Load org sync app credentials ──
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "feishu_org_sync"))
        setting = r.scalar_one_or_none()
        if not setting or not setting.value.get("app_id"):
            logger.warning("No feishu_org_sync setting found. Cannot resolve user_ids. Skipping backfill.")
            logger.info("You can still run Sync Now from the UI after configuring org sync.")
            return

        app_id = setting.value["app_id"]
        app_secret = setting.value["app_secret"]

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
        r = await db.execute(
            select(User).where(
                User.feishu_open_id.isnot(None),
                (User.feishu_user_id.is_(None)) | (User.feishu_user_id == ""),
            )
        )
        users_to_fill = r.scalars().all()
        logger.info(f"Found {len(users_to_fill)} users needing user_id backfill")

        filled_count = 0
        for user in users_to_fill:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://open.feishu.cn/open-apis/contact/v3/users/{user.feishu_open_id}",
                        params={"user_id_type": "open_id"},
                        headers={"Authorization": f"Bearer {app_token}"},
                    )
                    data = resp.json()
                    if data.get("code") == 0:
                        user_id = data.get("data", {}).get("user", {}).get("user_id", "")
                        if user_id:
                            user.feishu_user_id = user_id
                            filled_count += 1
                            logger.info(f"  Filled user_id for {user.display_name or user.username}: {user_id}")
                        else:
                            logger.warning(f"  No user_id returned for {user.display_name or user.username} — App may lack permission")
                    else:
                        # open_id might be from a different app, try email
                        if user.email and "@" in user.email and not user.email.endswith("@feishu.local"):
                            resp2 = await client.post(
                                "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id",
                                json={"emails": [user.email]},
                                headers={"Authorization": f"Bearer {app_token}"},
                                params={"user_id_type": "user_id"},
                            )
                            data2 = resp2.json()
                            if data2.get("code") == 0:
                                user_list = data2.get("data", {}).get("user_list", [])
                                for u in user_list:
                                    uid = u.get("user_id")
                                    if uid:
                                        user.feishu_user_id = uid
                                        filled_count += 1
                                        logger.info(f"  Filled user_id via email for {user.display_name}: {uid}")
                                        break
                        else:
                            logger.warning(f"  Cannot resolve {user.display_name} (code={data.get('code')}, msg={data.get('msg')})")
            except Exception as e:
                logger.error(f"  Error resolving {user.display_name}: {e}")

        await db.commit()
        logger.info(f"Backfilled user_id for {filled_count}/{len(users_to_fill)} users")

        # ── Step 2: Backfill user_id for OrgMembers ──
        logger.info("=== Step 2: Backfill feishu_user_id for OrgMembers ===")
        r = await db.execute(
            select(OrgMember).where(
                OrgMember.feishu_open_id.isnot(None),
                (OrgMember.feishu_user_id.is_(None)) | (OrgMember.feishu_user_id == ""),
            )
        )
        members_to_fill = r.scalars().all()
        logger.info(f"Found {len(members_to_fill)} org members needing user_id backfill")

        member_filled = 0
        for member in members_to_fill:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://open.feishu.cn/open-apis/contact/v3/users/{member.feishu_open_id}",
                        params={"user_id_type": "open_id"},
                        headers={"Authorization": f"Bearer {app_token}"},
                    )
                    data = resp.json()
                    if data.get("code") == 0:
                        user_id = data.get("data", {}).get("user", {}).get("user_id", "")
                        if user_id:
                            member.feishu_user_id = user_id
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
                if m.feishu_user_id:
                    s += 10
                if m.feishu_open_id:
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
                if dup.feishu_user_id and not primary.feishu_user_id:
                    primary.feishu_user_id = dup.feishu_user_id
                if dup.email and primary.email != dup.email and dup.email:
                    if not primary.email:
                        primary.email = dup.email
                # Clear unique field before delete
                dup.feishu_open_id = None
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
            # Priority: has real email > has feishu_user_id > has feishu_open_id > oldest
            def score(u):
                s = 0
                if u.email and "@" in u.email and not u.email.endswith("@feishu.local"):
                    s += 100  # Real email = likely registered user
                if u.feishu_user_id:
                    s += 10
                if u.feishu_open_id:
                    s += 1
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
                if dup.feishu_open_id and not primary.feishu_open_id:
                    primary.feishu_open_id = dup.feishu_open_id
                if dup.feishu_union_id and not primary.feishu_union_id:
                    primary.feishu_union_id = dup.feishu_union_id
                # Clear unique fields on duplicate before delete to avoid constraint violations
                dup.feishu_open_id = None
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
                u_r = await db.execute(
                    select(User).where(User.feishu_open_id == old_id)
                )
                u = u_r.scalar_one_or_none()
                if u and u.feishu_user_id:
                    new_conv = f"feishu_p2p_{u.feishu_user_id}"
                    sess.external_conv_id = new_conv
                    updated_sessions += 1
                    logger.info(f"  Updated session conv_id: {old_conv} -> {new_conv}")

        await db.commit()
        logger.info(f"Updated {updated_sessions}/{len(sessions)} session conv_ids")

    logger.info("=== Migration complete ===")


if __name__ == "__main__":
    asyncio.run(main())
