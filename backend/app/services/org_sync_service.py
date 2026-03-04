"""Feishu organization structure sync service.

Pulls departments and members from Feishu Contact API and upserts into local DB.
"""

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.org import OrgDepartment, OrgMember
from app.models.system_settings import SystemSetting
from app.models.user import User
from app.core.security import hash_password

logger = logging.getLogger(__name__)

FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
FEISHU_DEPT_CHILDREN_URL = "https://open.feishu.cn/open-apis/contact/v3/departments"
FEISHU_USERS_URL = "https://open.feishu.cn/open-apis/contact/v3/users/find_by_department"


class OrgSyncService:
    """Sync org structure from Feishu into local database."""

    async def _get_feishu_config(self, db: AsyncSession) -> dict | None:
        """Load Feishu org sync config from system_settings."""
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "feishu_org_sync")
        )
        setting = result.scalar_one_or_none()
        if not setting:
            return None
        return setting.value

    async def _get_app_token(self, app_id: str, app_secret: str) -> str:
        """Get Feishu tenant_access_token (same as app_access_token for self-built apps)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            data = resp.json()
            print(f"[OrgSync] Token response: code={data.get('code')}, msg={data.get('msg')}")
            token = data.get("tenant_access_token") or data.get("app_access_token") or ""
            return token

    async def _fetch_departments(self, token: str, parent_id: str = "0") -> list[dict]:
        """Recursively fetch all departments from Feishu."""
        all_depts = []
        page_token = ""
        while True:
            async with httpx.AsyncClient() as client:
                url = f"{FEISHU_DEPT_CHILDREN_URL}/{parent_id}/children"
                params = {
                    "department_id_type": "open_department_id",
                    "page_size": "50",
                    "fetch_child": "true",
                }
                if page_token:
                    params["page_token"] = page_token

                print(f"[OrgSync] GET {url} params={params}")
                resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
                data = resp.json()
                print(f"[OrgSync] Dept response: code={data.get('code')}, msg={data.get('msg')}, items={len(data.get('data', {}).get('items', []))}")

                if data.get("code") != 0:
                    print(f"[OrgSync] Dept API error: {data}")
                    break

                items = data.get("data", {}).get("items", [])
                if items and not all_depts:  # Print first raw item for debugging
                    print(f"[OrgSync] RAW first dept item keys: {list(items[0].keys())}")
                    print(f"[OrgSync] RAW first dept item: {items[0]}")
                for item in items:
                    print(f"[OrgSync]   dept: {item.get('name')} (id={item.get('open_department_id')})")
                all_depts.extend(items)

                if not data.get("data", {}).get("has_more"):
                    break
                page_token = data["data"].get("page_token", "")

        # If fetch_child=true didn't work (no items), try without recursion + manual recurse
        if not all_depts:
            print(f"[OrgSync] fetch_child=true returned nothing, trying simple list...")
            all_depts = await self._fetch_departments_simple(token, parent_id)

        return all_depts

    async def _fetch_departments_simple(self, token: str, parent_id: str = "0") -> list[dict]:
        """Fetch departments without fetch_child, manually recurse."""
        all_depts = []
        page_token = ""
        while True:
            async with httpx.AsyncClient() as client:
                url = f"{FEISHU_DEPT_CHILDREN_URL}/{parent_id}/children"
                params = {"department_id_type": "open_department_id", "page_size": "50"}
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
                data = resp.json()
                print(f"[OrgSync] Simple dept response (parent={parent_id}): code={data.get('code')}, items={len(data.get('data', {}).get('items', []))}")

                if data.get("code") != 0:
                    print(f"[OrgSync] Simple dept error: {data}")
                    break

                items = data.get("data", {}).get("items", [])
                all_depts.extend(items)
                if not data.get("data", {}).get("has_more"):
                    break
                page_token = data["data"].get("page_token", "")

        # Recurse into children and inject parent_department_id
        for dept in list(all_depts):
            dept_id = dept.get("open_department_id", "")
            if dept_id:
                children = await self._fetch_departments_simple(token, dept_id)
                # Set parent_department_id explicitly (API may not return it)
                for child in children:
                    if not child.get("parent_department_id"):
                        child["parent_department_id"] = dept_id
                all_depts.extend(children)

        return all_depts

    async def _fetch_department_users(self, token: str, dept_id: str) -> list[dict]:
        """Fetch all users in a department."""
        all_users = []
        page_token = ""
        while True:
            async with httpx.AsyncClient() as client:
                params = {
                    "department_id_type": "open_department_id",
                    "department_id": dept_id,
                    "page_size": "50",
                }
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(
                    FEISHU_USERS_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                print(f"[OrgSync] Users response (dept={dept_id}): code={data.get('code')}, items={len(data.get('data', {}).get('items', []))}")

                if data.get("code") != 0:
                    print(f"[OrgSync] Users API error: {data}")
                    break

                items = data.get("data", {}).get("items", [])
                if items and not all_users:  # Print first raw user for debugging
                    print(f"[OrgSync] RAW first user item keys: {list(items[0].keys())}")
                    print(f"[OrgSync] RAW first user item: {items[0]}")
                all_users.extend(items)
                if not data.get("data", {}).get("has_more"):
                    break
                page_token = data["data"].get("page_token", "")
        return all_users

    async def full_sync(self) -> dict:
        """Run a full org sync from Feishu. Returns stats."""
        async with async_session() as db:
            config = await self._get_feishu_config(db)
            if not config:
                return {"error": "未配置飞书组织架构同步信息"}

            app_id = config.get("app_id")
            app_secret = config.get("app_secret")
            if not app_id or not app_secret:
                return {"error": "缺少 App ID 或 App Secret"}

            try:
                token = await self._get_app_token(app_id, app_secret)
                if not token:
                    return {"error": "获取飞书 token 失败"}
                print(f"[OrgSync] Got token: {token[:20]}...")
            except Exception as e:
                return {"error": f"连接飞书失败: {str(e)[:100]}"}

            now = datetime.now(timezone.utc)
            dept_count = 0
            member_count = 0
            user_count = 0

            # Resolve tenant_id from the first admin user
            admin_result = await db.execute(
                select(User).where(User.role == "platform_admin").limit(1)
            )
            admin_user = admin_result.scalar_one_or_none()
            tenant_id = admin_user.tenant_id if admin_user else None

            # --- Sync departments ---
            try:
                depts = await self._fetch_departments(token, "0")
                print(f"[OrgSync] Total departments fetched: {len(depts)}")

                for d in depts:
                    feishu_id = d.get("open_department_id", "")
                    if not feishu_id:
                        continue
                    result = await db.execute(
                        select(OrgDepartment).where(OrgDepartment.feishu_id == feishu_id)
                    )
                    dept = result.scalar_one_or_none()
                    if dept:
                        dept.name = d.get("name", dept.name)
                        dept.member_count = d.get("member_count", 0)
                        dept.synced_at = now
                    else:
                        dept = OrgDepartment(
                            feishu_id=feishu_id,
                            name=d.get("name", ""),
                            member_count=d.get("member_count", 0),
                            path=d.get("name", ""),
                            tenant_id=tenant_id,
                            synced_at=now,
                        )
                        db.add(dept)
                    dept_count += 1

                await db.flush()

                # Build feishu_id -> db_id + parent mapping
                dept_map = {}
                all_result = await db.execute(select(OrgDepartment))
                for dept in all_result.scalars().all():
                    if dept.feishu_id:
                        dept_map[dept.feishu_id] = dept

                # Set parent_id from Feishu parent info
                for d in depts:
                    feishu_id = d.get("open_department_id", "")
                    parent_feishu_id = d.get("parent_department_id", "")
                    if feishu_id in dept_map and parent_feishu_id and parent_feishu_id in dept_map:
                        dept_map[feishu_id].parent_id = dept_map[parent_feishu_id].id

                await db.flush()
            except Exception as e:
                import traceback
                traceback.print_exc()
                logger.error(f"[OrgSync] Department sync failed: {e}")
                return {"error": f"部门同步失败: {str(e)[:200]}"}

            # --- Sync members ---
            try:
                all_dept_result = await db.execute(select(OrgDepartment))
                departments = all_dept_result.scalars().all()

                for dept in departments:
                    if not dept.feishu_id:
                        continue
                    users = await self._fetch_department_users(token, dept.feishu_id)
                    if users:
                        logger.info(f"[OrgSync] dept={dept.name} got {len(users)} users, first user keys={list(users[0].keys())}, open_id={users[0].get('open_id','')!r}, user_id={users[0].get('user_id','')!r}")
                    for u in users:
                        open_id = u.get("open_id", "")
                        user_id = u.get("user_id", "")
                        if not open_id and not user_id:
                            logger.warning(f"[OrgSync] Skipping user with no open_id and no user_id: {u.get('name','?')}")
                            continue

                        # Try to find existing member by open_id or user_id
                        member = None
                        if open_id:
                            result = await db.execute(
                                select(OrgMember).where(OrgMember.feishu_open_id == open_id)
                            )
                            member = result.scalar_one_or_none()
                        if not member and user_id:
                            result = await db.execute(
                                select(OrgMember).where(OrgMember.feishu_user_id == user_id)
                            )
                            member = result.scalar_one_or_none()

                        if member:
                            member.name = u.get("name", member.name)
                            member.email = u.get("email", member.email)
                            member.avatar_url = u.get("avatar", {}).get("avatar_origin", member.avatar_url)
                            member.title = (u.get("job_title") or u.get("description") or member.title or "")[:200]
                            member.department_id = dept.id
                            member.department_path = dept.path or dept.name
                            member.phone = u.get("mobile", member.phone)
                            # Only set open_id if not already present (avoid overwriting OAuth-set IDs)
                            if open_id and not member.feishu_open_id:
                                member.feishu_open_id = open_id
                            if user_id:
                                member.feishu_user_id = user_id
                            member.synced_at = now
                        else:
                            member = OrgMember(
                                feishu_open_id=open_id or None,
                                feishu_user_id=user_id or None,
                                name=u.get("name", ""),
                                email=u.get("email", ""),
                                avatar_url=u.get("avatar", {}).get("avatar_origin", ""),
                                title=(u.get("job_title") or u.get("description") or "")[:200],
                                department_id=dept.id,
                                department_path=dept.path or dept.name,
                                phone=u.get("mobile", ""),
                                tenant_id=tenant_id,
                                synced_at=now,
                            )
                            db.add(member)
                        # Ensure tenant_id is set on existing members
                        if member.tenant_id is None and tenant_id:
                            member.tenant_id = tenant_id
                        member_count += 1

                        # --- Auto-create/update platform User ---
                        platform_user = None
                        if open_id:
                            pu_result = await db.execute(
                                select(User).where(User.feishu_open_id == open_id)
                            )
                            platform_user = pu_result.scalar_one_or_none()
                        if not platform_user and user_id:
                            pu_result = await db.execute(
                                select(User).where(User.feishu_user_id == user_id)
                            )
                            platform_user = pu_result.scalar_one_or_none()

                        member_name = u.get("name", "")
                        if platform_user:
                            # Update existing user info
                            platform_user.display_name = member_name or platform_user.display_name
                            if open_id and not platform_user.feishu_open_id:
                                platform_user.feishu_open_id = open_id
                            if user_id and not platform_user.feishu_user_id:
                                platform_user.feishu_user_id = user_id
                            if tenant_id and not platform_user.tenant_id:
                                platform_user.tenant_id = tenant_id
                        else:
                            # Create new user
                            username_base = f"feishu_{user_id or (open_id[:16] if open_id else uuid.uuid4().hex[:8])}"
                            email = u.get("email") or f"{username_base}@feishu.local"
                            platform_user = User(
                                username=username_base,
                                email=email,
                                password_hash=hash_password(uuid.uuid4().hex),
                                display_name=member_name,
                                role="member",
                                feishu_open_id=open_id or None,
                                feishu_user_id=user_id or None,
                                tenant_id=tenant_id,
                            )
                            db.add(platform_user)
                            user_count += 1
            except Exception as e:
                import traceback
                traceback.print_exc()
                logger.error(f"[OrgSync] Member sync failed: {e}")
                return {"error": f"成员同步失败: {str(e)[:200]}", "departments": dept_count}

            # Update last sync time
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == "feishu_org_sync")
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.value = {**setting.value, "last_synced_at": now.isoformat()}

            await db.commit()

            stats = {"departments": dept_count, "members": member_count, "users_created": user_count, "synced_at": now.isoformat()}
            print(f"[OrgSync] Complete: {stats}")
            return stats


org_sync_service = OrgSyncService()
