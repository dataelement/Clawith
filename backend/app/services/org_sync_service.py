"""Directory sync service for Feishu and WeCom organization structures."""

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pypinyin import pinyin, Style

from app.core.security import hash_password
from app.database import async_session
from app.models.org import OrgDepartment, OrgMember
from app.models.system_settings import SystemSetting
from app.models.user import User

ORG_SYNC_KEY = "org_sync"
LEGACY_FEISHU_SYNC_KEY = "feishu_org_sync"

FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
FEISHU_DEPT_CHILDREN_URL = "https://open.feishu.cn/open-apis/contact/v3/departments"
FEISHU_USERS_URL = "https://open.feishu.cn/open-apis/contact/v3/users/find_by_department"

WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_DEPARTMENTS_URL = "https://qyapi.weixin.qq.com/cgi-bin/department/list"
WECOM_USERS_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/list"


class OrgSyncService:
    """Sync org structure from the configured directory provider into local DB."""

    async def _get_stored_config(self, db: AsyncSession) -> dict:
        """Return normalized org sync config including stored secrets."""
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == ORG_SYNC_KEY))
        setting = result.scalar_one_or_none()
        if setting and isinstance(setting.value, dict):
            return self._normalize_setting_value(setting.value)

        legacy_result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == LEGACY_FEISHU_SYNC_KEY)
        )
        legacy_setting = legacy_result.scalar_one_or_none()
        if legacy_setting and isinstance(legacy_setting.value, dict):
            return self._normalize_setting_value(
                {
                    "provider": "feishu",
                    "feishu": legacy_setting.value,
                    "wecom": {},
                }
            )

        return self._normalize_setting_value({})

    async def get_public_config(self, db: AsyncSession) -> dict:
        """Return normalized org sync config for the UI without provider secrets."""
        return self._normalize_setting_value(await self._get_stored_config(db), include_secrets=False)

    def _normalize_setting_value(self, value: dict | None, include_secrets: bool = True) -> dict:
        raw = value or {}
        provider = raw.get("provider")
        if provider not in {"feishu", "wecom"}:
            provider = "feishu"

        feishu = raw.get("feishu")
        wecom = raw.get("wecom")

        # Accept the old flat feishu shape transparently.
        if not isinstance(feishu, dict) and any(k in raw for k in ("app_id", "app_secret", "last_synced_at")):
            feishu = {
                "app_id": raw.get("app_id", ""),
                "app_secret": raw.get("app_secret", "") if include_secrets else "",
                "last_synced_at": raw.get("last_synced_at"),
            }

        return {
            "provider": provider,
            "feishu": {
                "app_id": (feishu or {}).get("app_id", ""),
                "app_secret": (feishu or {}).get("app_secret", "") if include_secrets else "",
                "has_secret": bool((feishu or {}).get("app_secret")),
                "last_synced_at": (feishu or {}).get("last_synced_at"),
            },
            "wecom": {
                "corp_id": (wecom or {}).get("corp_id", ""),
                "corp_secret": (wecom or {}).get("corp_secret", "") if include_secrets else "",
                "has_secret": bool((wecom or {}).get("corp_secret")),
                "last_synced_at": (wecom or {}).get("last_synced_at"),
            },
        }

    async def save_config(self, db: AsyncSession, value: dict) -> dict:
        """Persist normalized org sync config while preserving existing secrets."""
        existing = await self._get_stored_config(db)
        normalized = self._normalize_setting_value(value)

        existing_feishu = existing.get("feishu", {})
        existing_wecom = existing.get("wecom", {})

        if not normalized["feishu"].get("app_secret"):
            normalized["feishu"]["app_secret"] = existing_feishu.get("app_secret", "")
        if not normalized["wecom"].get("corp_secret"):
            normalized["wecom"]["corp_secret"] = existing_wecom.get("corp_secret", "")
        if not normalized["feishu"].get("last_synced_at"):
            normalized["feishu"]["last_synced_at"] = existing_feishu.get("last_synced_at")
        if not normalized["wecom"].get("last_synced_at"):
            normalized["wecom"]["last_synced_at"] = existing_wecom.get("last_synced_at")

        result = await db.execute(select(SystemSetting).where(SystemSetting.key == ORG_SYNC_KEY))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = normalized
        else:
            setting = SystemSetting(key=ORG_SYNC_KEY, value=normalized)
            db.add(setting)
        await db.commit()
        return normalized

    async def get_active_provider(self, db: AsyncSession) -> tuple[str, dict, dict]:
        """Return provider name, provider config, and full normalized setting."""
        setting = await self._get_stored_config(db)
        provider = setting.get("provider", "feishu")
        return provider, setting.get(provider, {}), setting

    async def _set_last_synced_at(
        self, db: AsyncSession, setting_value: dict, provider: str, synced_at: str
    ) -> None:
        value = self._normalize_setting_value(setting_value)
        value.setdefault(provider, {})
        value[provider]["last_synced_at"] = synced_at

        result = await db.execute(select(SystemSetting).where(SystemSetting.key == ORG_SYNC_KEY))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(SystemSetting(key=ORG_SYNC_KEY, value=value))

    async def _get_feishu_token(self, app_id: str, app_secret: str) -> tuple[str, dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                FEISHU_APP_TOKEN_URL,
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            token = data.get("tenant_access_token") or data.get("app_access_token") or ""
            return token, data

    async def _fetch_feishu_departments(self, token: str, parent_id: str = "0") -> list[dict]:
        all_depts: list[dict] = []
        page_token = ""
        while True:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{FEISHU_DEPT_CHILDREN_URL}/{parent_id}/children",
                    params={
                        "department_id_type": "open_department_id",
                        "page_size": "50",
                        "fetch_child": "true",
                        **({"page_token": page_token} if page_token else {}),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"Feishu department API failed: code={data.get('code')} msg={data.get('msg')}"
                )
            all_depts.extend(data.get("data", {}).get("items", []))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
        if not all_depts:
            return await self._fetch_feishu_departments_simple(token, parent_id)
        return all_depts

    async def _fetch_feishu_departments_simple(
        self, token: str, parent_id: str = "0"
    ) -> list[dict]:
        all_depts: list[dict] = []
        page_token = ""
        while True:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{FEISHU_DEPT_CHILDREN_URL}/{parent_id}/children",
                    params={
                        "department_id_type": "open_department_id",
                        "page_size": "50",
                        **({"page_token": page_token} if page_token else {}),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"Feishu department API failed: code={data.get('code')} msg={data.get('msg')}"
                )
            items = data.get("data", {}).get("items", [])
            all_depts.extend(items)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")

        for dept in list(all_depts):
            dept_id = dept.get("open_department_id")
            if not dept_id:
                continue
            children = await self._fetch_feishu_departments_simple(token, dept_id)
            for child in children:
                child.setdefault("parent_department_id", dept_id)
            all_depts.extend(children)
        return all_depts

    async def _fetch_feishu_department_users(self, token: str, department_id: str) -> list[dict]:
        users: list[dict] = []
        page_token = ""
        while True:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    FEISHU_USERS_URL,
                    params={
                        "department_id_type": "open_department_id",
                        "department_id": department_id,
                        "page_size": "50",
                        **({"page_token": page_token} if page_token else {}),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"Feishu user API failed: code={data.get('code')} msg={data.get('msg')}"
                )
            users.extend(data.get("data", {}).get("items", []))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
        return users

    async def _get_wecom_token(self, corp_id: str, corp_secret: str) -> tuple[str, dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                WECOM_TOKEN_URL,
                params={
                    "corpid": corp_id,
                    "corpsecret": corp_secret,
                },
            )
            data = resp.json()
            token = data.get("access_token", "")
            return token, data

    async def _fetch_wecom_departments(self, token: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                WECOM_DEPARTMENTS_URL,
                params={"access_token": token},
            )
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(
                f"WeCom department API failed: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            )
        return data.get("department", [])

    async def _fetch_wecom_department_users(
        self,
        token: str,
        department_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict]:
        owned_client = client is None
        if owned_client:
            client = httpx.AsyncClient(timeout=20)
        assert client is not None
        try:
            for attempt in range(4):
                resp = await client.get(
                    WECOM_USERS_URL,
                    params={
                        "access_token": token,
                        "department_id": department_id,
                        "fetch_child": 0,
                    },
                )
                data = resp.json()
                if data.get("errcode") != 45033:
                    break
                await asyncio.sleep(0.5 * (attempt + 1))
        finally:
            if owned_client:
                await client.aclose()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(
                f"WeCom user API failed: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            )
        return data.get("userlist", [])

    async def _fetch_wecom_users_by_department(
        self,
        token: str,
        department_ids: list[str],
        concurrency: int = 4,
    ) -> dict[str, list[dict]]:
        semaphore = asyncio.Semaphore(concurrency)
        results: dict[str, list[dict]] = {}

        async with httpx.AsyncClient(timeout=20) as client:
            async def fetch_one(department_id: str) -> None:
                async with semaphore:
                    results[department_id] = await self._fetch_wecom_department_users(
                        token,
                        department_id,
                        client=client,
                    )

            await asyncio.gather(*(fetch_one(department_id) for department_id in department_ids))
        return results

    async def _resolve_tenant_id(self, db: AsyncSession) -> uuid.UUID | None:
        admin_result = await db.execute(
            select(User).where(User.role == "platform_admin").limit(1)
        )
        admin_user = admin_result.scalar_one_or_none()
        return admin_user.tenant_id if admin_user else None

    async def _upsert_feishu_departments(
        self, db: AsyncSession, departments: list[dict], tenant_id: uuid.UUID | None, now: datetime
    ) -> tuple[int, dict[str, OrgDepartment]]:
        provider = "feishu"
        count = 0
        for item in departments:
            external_id = item.get("open_department_id")
            if not external_id:
                continue
            result = await db.execute(
                select(OrgDepartment).where(
                    OrgDepartment.sync_provider == provider,
                    OrgDepartment.feishu_id == external_id,
                )
            )
            dept = result.scalar_one_or_none()
            if dept:
                dept.name = item.get("name", dept.name)
                dept.member_count = item.get("member_count", 0)
                dept.synced_at = now
                dept.tenant_id = dept.tenant_id or tenant_id
            else:
                dept = OrgDepartment(
                    feishu_id=external_id,
                    name=item.get("name", ""),
                    member_count=item.get("member_count", 0),
                    path=item.get("name", ""),
                    tenant_id=tenant_id,
                    synced_at=now,
                    sync_provider=provider,
                )
                db.add(dept)
            count += 1

        await db.flush()
        result = await db.execute(select(OrgDepartment).where(OrgDepartment.sync_provider == provider))
        dept_map = {dept.feishu_id: dept for dept in result.scalars().all() if dept.feishu_id}
        for item in departments:
            external_id = item.get("open_department_id")
            parent_external_id = item.get("parent_department_id")
            if external_id in dept_map and parent_external_id and parent_external_id in dept_map:
                dept_map[external_id].parent_id = dept_map[parent_external_id].id
        self._rebuild_department_paths(dept_map)
        await db.flush()
        return count, dept_map

    async def _upsert_wecom_departments(
        self, db: AsyncSession, departments: list[dict], tenant_id: uuid.UUID | None, now: datetime
    ) -> tuple[int, dict[str, OrgDepartment]]:
        provider = "wecom"
        existing_result = await db.execute(
            select(OrgDepartment).where(OrgDepartment.sync_provider == provider)
        )
        existing_departments = {
            dept.wecom_id: dept
            for dept in existing_result.scalars().all()
            if dept.wecom_id
        }
        count = 0
        for item in departments:
            external_id = str(item.get("id", "")).strip()
            if not external_id:
                continue
            dept = existing_departments.get(external_id)
            if dept:
                dept.name = item.get("name", dept.name)
                dept.member_count = item.get("member_num", dept.member_count)
                dept.synced_at = now
                dept.tenant_id = dept.tenant_id or tenant_id
            else:
                dept = OrgDepartment(
                    wecom_id=external_id,
                    name=item.get("name", ""),
                    member_count=item.get("member_num", 0),
                    path=item.get("name", ""),
                    tenant_id=tenant_id,
                    synced_at=now,
                    sync_provider=provider,
                )
                db.add(dept)
                existing_departments[external_id] = dept
            count += 1

        await db.flush()
        dept_map = {
            dept.wecom_id: dept
            for dept in existing_departments.values()
            if dept.wecom_id
        }
        for item in departments:
            external_id = str(item.get("id", "")).strip()
            parent_external_id = str(item.get("parentid", "")).strip()
            if external_id in dept_map and parent_external_id and parent_external_id in dept_map:
                dept_map[external_id].parent_id = dept_map[parent_external_id].id
        self._rebuild_department_paths(dept_map)
        await db.flush()
        return count, dept_map

    def _rebuild_department_paths(self, dept_map: dict[str, OrgDepartment]) -> None:
        by_db_id = {dept.id: dept for dept in dept_map.values()}

        def build_path(dept: OrgDepartment) -> str:
            names = [dept.name]
            current = dept
            seen: set[uuid.UUID] = set()
            while current.parent_id and current.parent_id not in seen and current.parent_id in by_db_id:
                seen.add(current.parent_id)
                current = by_db_id[current.parent_id]
                names.append(current.name)
            names.reverse()
            return " / ".join([name for name in names if name])

        for dept in dept_map.values():
            dept.path = build_path(dept)

    async def _upsert_feishu_members(
        self,
        db: AsyncSession,
        token: str,
        tenant_id: uuid.UUID | None,
        departments: dict[str, OrgDepartment],
        now: datetime,
    ) -> tuple[int, int]:
        provider = "feishu"
        member_count = 0
        user_count = 0
        for external_dept_id, dept in departments.items():
            users = await self._fetch_feishu_department_users(token, external_dept_id)
            dept.member_count = len(users)
            for item in users:
                open_id = item.get("open_id", "")
                user_id = item.get("user_id", "")
                if not open_id and not user_id:
                    continue
                result = None
                if user_id:
                    result = await db.execute(
                        select(OrgMember).where(
                            OrgMember.sync_provider == provider,
                            OrgMember.feishu_user_id == user_id,
                        )
                    )
                    member = result.scalar_one_or_none()
                else:
                    member = None
                if not member and open_id:
                    result = await db.execute(
                        select(OrgMember).where(
                            OrgMember.sync_provider == provider,
                            OrgMember.feishu_open_id == open_id,
                        )
                    )
                    member = result.scalar_one_or_none()

                avatar = item.get("avatar", {}) if isinstance(item.get("avatar"), dict) else {}
                member_email = item.get("email", "")
                if member:
                    member.name = item.get("name", member.name)
                    member.email = member_email or member.email
                    member.avatar_url = avatar.get("avatar_origin", member.avatar_url)
                    member.title = (item.get("job_title") or item.get("description") or member.title or "")[:200]
                    member.department_id = dept.id
                    member.department_path = dept.path or dept.name
                    member.phone = item.get("mobile", member.phone)
                    member.synced_at = now
                    if open_id:
                        member.feishu_open_id = open_id
                    if user_id:
                        member.feishu_user_id = user_id
                else:
                    member = OrgMember(
                        feishu_open_id=open_id or None,
                        feishu_user_id=user_id or None,
                        name=item.get("name", ""),
                        email=member_email or None,
                        avatar_url=avatar.get("avatar_origin", ""),
                        title=(item.get("job_title") or item.get("description") or "")[:200],
                        department_id=dept.id,
                        department_path=dept.path or dept.name,
                        phone=item.get("mobile", ""),
                        tenant_id=tenant_id,
                        synced_at=now,
                        sync_provider=provider,
                    )
                    db.add(member)
                if member.tenant_id is None and tenant_id:
                    member.tenant_id = tenant_id
                member_count += 1

                created = await self._upsert_platform_user_for_feishu(db, item, tenant_id, user_id, open_id)
                user_count += int(created)
        return member_count, user_count

    async def _upsert_wecom_members(
        self,
        db: AsyncSession,
        token: str,
        tenant_id: uuid.UUID | None,
        departments: dict[str, OrgDepartment],
        now: datetime,
    ) -> tuple[int, int]:
        provider = "wecom"
        users_by_department = await self._fetch_wecom_users_by_department(
            token,
            list(departments.keys()),
        )

        existing_members_result = await db.execute(
            select(OrgMember).where(OrgMember.sync_provider == provider)
        )
        existing_members = {
            member.wecom_user_id: member
            for member in existing_members_result.scalars().all()
            if member.wecom_user_id
        }

        all_items = [
            item
            for users in users_by_department.values()
            for item in users
            if str(item.get("userid", "")).strip()
        ]
        emails = {
            str(item.get("email", "")).strip()
            for item in all_items
            if "@" in str(item.get("email", "")).strip()
        }
        usernames = {
            f"wecom_{str(item.get('userid', '')).strip()}"
            for item in all_items
            if str(item.get("userid", "")).strip()
        }

        existing_users_by_email: dict[str, User] = {}
        if emails:
            users_result = await db.execute(select(User).where(User.email.in_(sorted(emails))))
            existing_users_by_email = {
                user.email: user
                for user in users_result.scalars().all()
                if user.email
            }

        existing_users_by_username: dict[str, User] = {}
        if usernames:
            users_result = await db.execute(select(User).where(User.username.in_(sorted(usernames))))
            existing_users_by_username = {
                user.username: user
                for user in users_result.scalars().all()
            }

        member_count = 0
        user_count = 0
        for external_dept_id, dept in departments.items():
            users = users_by_department.get(external_dept_id, [])
            dept.member_count = len(users)
            for item in users:
                user_id = str(item.get("userid", "")).strip()
                if not user_id:
                    continue
                member = existing_members.get(user_id)
                if member:
                    member.name = item.get("name", member.name)
                    member.email = item.get("email") or member.email
                    member.avatar_url = item.get("avatar") or member.avatar_url
                    member.title = (item.get("position") or member.title or "")[:200]
                    member.department_id = dept.id
                    member.department_path = dept.path or dept.name
                    member.phone = item.get("mobile") or member.phone
                    member.synced_at = now
                else:
                    member = OrgMember(
                        wecom_user_id=user_id,
                        name=item.get("name", ""),
                        email=item.get("email") or None,
                        avatar_url=item.get("avatar") or "",
                        title=(item.get("position") or "")[:200],
                        department_id=dept.id,
                        department_path=dept.path or dept.name,
                        phone=item.get("mobile") or "",
                        tenant_id=tenant_id,
                        synced_at=now,
                        sync_provider=provider,
                    )
                    db.add(member)
                    existing_members[user_id] = member
                if member.tenant_id is None and tenant_id:
                    member.tenant_id = tenant_id
                member_count += 1

                created = self._upsert_platform_user_for_wecom(
                    db,
                    item,
                    tenant_id,
                    user_id,
                    existing_users_by_email,
                    existing_users_by_username,
                )
                user_count += int(created)
        return member_count, user_count

    async def _upsert_platform_user_for_feishu(
        self,
        db: AsyncSession,
        item: dict,
        tenant_id: uuid.UUID | None,
        user_id: str,
        open_id: str,
    ) -> bool:
        platform_user = None
        if user_id:
            result = await db.execute(select(User).where(User.feishu_user_id == user_id))
            platform_user = result.scalar_one_or_none()
        if not platform_user and open_id:
            result = await db.execute(select(User).where(User.feishu_open_id == open_id))
            platform_user = result.scalar_one_or_none()
        email = item.get("email", "")
        if not platform_user and email and "@" in email and not email.endswith("@feishu.local"):
            result = await db.execute(select(User).where(User.email == email))
            platform_user = result.scalar_one_or_none()

        if platform_user:
            platform_user.display_name = item.get("name", platform_user.display_name)
            if open_id:
                platform_user.feishu_open_id = open_id
            if user_id:
                platform_user.feishu_user_id = user_id
            if tenant_id and not platform_user.tenant_id:
                platform_user.tenant_id = tenant_id
            return False

        username_base = f"feishu_{user_id or (open_id[:16] if open_id else uuid.uuid4().hex[:8])}"
        db.add(
            User(
                username=username_base,
                email=email or f"{username_base}@feishu.local",
                password_hash=hash_password(uuid.uuid4().hex),
                display_name=item.get("name", username_base),
                role="member",
                feishu_open_id=open_id or None,
                feishu_user_id=user_id or None,
                tenant_id=tenant_id,
            )
        )
        return True

    def _upsert_platform_user_for_wecom(
        self,
        db: AsyncSession,
        item: dict,
        tenant_id: uuid.UUID | None,
        user_id: str,
        existing_users_by_email: dict[str, User],
        existing_users_by_username: dict[str, User],
    ) -> bool:
        email = str(item.get("email", "")).strip()
        platform_user = None
        if email and "@" in email:
            platform_user = existing_users_by_email.get(email)
        username_base = f"wecom_{user_id}"
        if not platform_user:
            platform_user = existing_users_by_username.get(username_base)

        if platform_user:
            platform_user.display_name = item.get("name", platform_user.display_name)
            platform_user.title = item.get("position") or platform_user.title
            if tenant_id and not platform_user.tenant_id:
                platform_user.tenant_id = tenant_id
            return False

        new_user = User(
            username=username_base,
            email=email or f"{username_base}@wecom.local",
            password_hash=hash_password(uuid.uuid4().hex),
            display_name=item.get("name", username_base),
            title=item.get("position") or None,
            role="member",
            tenant_id=tenant_id,
        )
        db.add(new_user)
        existing_users_by_username[new_user.username] = new_user
        if new_user.email:
            existing_users_by_email[new_user.email] = new_user
        return True

    async def full_sync(self) -> dict:
        """Run a full sync for the currently configured provider."""
        async with async_session() as db:
            provider, provider_config, setting = await self.get_active_provider(db)
            now = datetime.now(timezone.utc)
            tenant_id = await self._resolve_tenant_id(db)

            if provider == "feishu":
                app_id = provider_config.get("app_id", "").strip()
                app_secret = provider_config.get("app_secret", "").strip()
                if not app_id or not app_secret:
                    return {"error": "缺少飞书 App ID 或 App Secret", "provider": provider}
                try:
                    token, token_resp = await self._get_feishu_token(app_id, app_secret)
                except Exception as exc:
                    return {"error": f"连接飞书失败: {str(exc)[:100]}", "provider": provider}
                if not token:
                    return {
                        "error": f"获取飞书 token 失败 (code={token_resp.get('code')}: {token_resp.get('msg')})",
                        "provider": provider,
                    }

                try:
                    raw_departments = await self._fetch_feishu_departments(token)
                    department_count, dept_map = await self._upsert_feishu_departments(
                        db, raw_departments, tenant_id, now
                    )
                    member_count, user_count = await self._upsert_feishu_members(
                        db, token, tenant_id, dept_map, now
                    )
                except Exception as exc:
                    logger.exception("[OrgSync] Feishu sync failed")
                    return {"error": str(exc)[:200], "provider": provider}
            else:
                corp_id = provider_config.get("corp_id", "").strip()
                corp_secret = provider_config.get("corp_secret", "").strip()
                if not corp_id or not corp_secret:
                    return {"error": "缺少企微 Corp ID 或 Corp Secret", "provider": provider}
                try:
                    token, token_resp = await self._get_wecom_token(corp_id, corp_secret)
                except Exception as exc:
                    return {"error": f"连接企微失败: {str(exc)[:100]}", "provider": provider}
                if not token:
                    return {
                        "error": f"获取企微 token 失败 (errcode={token_resp.get('errcode')}: {token_resp.get('errmsg')})",
                        "provider": provider,
                    }

                try:
                    raw_departments = await self._fetch_wecom_departments(token)
                    department_count, dept_map = await self._upsert_wecom_departments(
                        db, raw_departments, tenant_id, now
                    )
                    member_count, user_count = await self._upsert_wecom_members(
                        db, token, tenant_id, dept_map, now
                    )
                except Exception as exc:
                    logger.exception("[OrgSync] WeCom sync failed")
                    return {"error": str(exc)[:200], "provider": provider}

            await self._set_last_synced_at(db, setting, provider, now.isoformat())
            await db.commit()

            stats = {
                "provider": provider,
                "departments": department_count,
                "members": member_count,
                "users_created": user_count,
                "synced_at": now.isoformat(),
            }
            logger.info(f"[OrgSync] Complete: {stats}")
            return stats


org_sync_service = OrgSyncService()
