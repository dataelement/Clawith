"""Generic organization sync adapter framework.

This module provides a base class for syncing org structure (departments/members)
from various identity providers (Feishu, DingTalk, WeCom, etc.).
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, delete, func, select, update

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import IdentityProvider
from app.models.org import OrgDepartment, OrgMember
from app.models.user import User
from pypinyin import pinyin, Style

from app.core.security import hash_password


def _normalize_contact(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass
class ExternalDepartment:
    """Standardized department info from external providers."""

    external_id: str
    name: str
    parent_external_id: str | None = None
    member_count: int = 0
    raw_data: dict = field(default_factory=dict)


@dataclass
class ExternalUser:
    """Standardized user info from external providers."""

    external_id: str  # The unique, platform-stable ID (e.g., userid)
    name: str
    open_id: str = ""  # OAuth open_id
    unionid: str = ""  # Union ID for cross-app identification
    email: str = ""
    avatar_url: str = ""
    title: str = ""
    department_external_id: str = ""
    department_path: str = ""
    department_ids: list[str] = field(default_factory=list)  # List of dept IDs from provider
    mobile: str = ""
    status: str = "active"
    raw_data: dict = field(default_factory=dict)


class BaseOrgSyncAdapter(ABC):
    """Abstract base class for organization sync adapters."""

    provider_type: str = ""

    def __init__(
        self,
        provider: IdentityProvider | None = None,
        config: dict | None = None,
        tenant_id: uuid.UUID | None = None,
    ):
        """Initialize adapter with provider config.

        Args:
            provider: IdentityProvider model from database
            config: Configuration dict (fallback if no provider record)
            tenant_id: Tenant ID for org sync
        """
        self.provider = provider
        self.config = config or {}
        self.tenant_id = tenant_id
        self._client: httpx.AsyncClient | None = None

        if provider and provider.config:
            self.config = provider.config

    @property
    @abstractmethod
    def api_base_url(self) -> str:
        """Base URL for provider API."""
        pass

    @abstractmethod
    async def get_access_token(self) -> str:
        """Get valid access token for API calls."""
        pass

    @abstractmethod
    async def fetch_departments(self) -> list[ExternalDepartment]:
        """Fetch all departments from provider.

        Returns:
            List of ExternalDepartment
        """
        pass

    @abstractmethod
    async def fetch_users(self, department_external_id: str) -> list[ExternalUser]:
        """Fetch users in a department.

        Args:
            department_external_id: External department ID

        Returns:
            List of ExternalUser
        """
        pass

    async def sync_org_structure(self, db: AsyncSession) -> dict[str, Any]:
        """Main sync function - syncs departments and members.

        Args:
            db: Database session

        Returns:
            Dict with sync results: {"departments": count, "members": count, "users_created": count, "profiles_synced": count, "errors": []}
        """
        errors = []
        dept_count = 0
        member_count = 0
        user_count = 0
        profile_count = 0
        sync_start = datetime.now()

        # Ensure provider exists
        provider = await self._ensure_provider(db)

        try:
            # Fetch and sync departments
            departments = await self.fetch_departments()
            for dept in departments:
                try:
                    async with db.begin_nested():
                        await self._upsert_department(db, provider, dept)
                    dept_count += 1
                except Exception as e:
                    errors.append(f"Department {dept.external_id}: {str(e)}")
                    logger.error(f"[OrgSync] Failed to sync department {dept.external_id}: {e}")

            # Fetch and sync users (from all departments)
            for dept in departments:
                try:
                    users = await self.fetch_users(dept.external_id)
                except Exception as e:
                    logger.error(f"[OrgSync] Failed to fetch users in department {dept.external_id}: {e}")
                    errors.append(f"Fetch users in dept {dept.external_id}: {str(e)}")
                    continue

                for user in users:
                    try:
                        async with db.begin_nested():
                            stats = await self._upsert_member(db, provider, user, dept.external_id)
                            if stats.get("user_created"):
                                user_count += 1
                            if stats.get("profile_synced"):
                                profile_count += 1
                        member_count += 1
                    except Exception as e:
                        logger.error(f"[OrgSync] Failed to sync member {user.external_id} ({user.name}): {e}")
                        errors.append(f"Member {user.external_id}: {str(e)}")

            # Update provider metadata if possible
            if self.provider:
                config = (self.provider.config or {}).copy()
                config["last_synced_at"] = datetime.now().isoformat()
                self.provider.config = config
                await db.flush()
                
                # Reconciliation: mark records not updated in this sync as deleted
                await self._reconcile(db, provider.id, sync_start)
                await db.flush()

                # Recalculate member counts for all departments (crucial for DingTalk/WeCom)
                await self._update_member_counts(db, provider.id)
                await db.flush()

        except Exception as e:
            import traceback
            logger.error(f"[OrgSync] Critical error during sync: {e}\n{traceback.format_exc()}")
            errors.append(f"Critical: {str(e)}")

        return {
            "departments": dept_count,
            "members": member_count,
            "users_created": user_count,
            "profiles_synced": profile_count,
            "errors": errors,
            "provider": self.provider_type,
            "synced_at": datetime.now().isoformat()
        }

    async def _reconcile(self, db: AsyncSession, provider_id: uuid.UUID, sync_start: datetime):
        """Mark records that were not updated in this sync as deleted."""
        
        # 1. Members reconciled
        await db.execute(
            update(OrgMember)
            .where(OrgMember.provider_id == provider_id)
            .where(OrgMember.synced_at < sync_start)
            .where(OrgMember.status != "deleted")
            .values(status="deleted", synced_at=datetime.now())
        )
        
        # 2. Departments reconciled
        await db.execute(
            update(OrgDepartment)
            .where(OrgDepartment.provider_id == provider_id)
            .where(OrgDepartment.synced_at < sync_start)
            .where(OrgDepartment.status != "deleted")
            .values(status="deleted", synced_at=datetime.now())
        )

    async def _update_member_counts(self, db: AsyncSession, provider_id: uuid.UUID):
        """Update member_count for all departments to include all their recursive sub-department members."""
        from sqlalchemy import update, select, func

        # 1. Update all departments to show their DIRECT member counts
        direct_subquery = (
            select(func.count(OrgMember.id))
            .where(OrgMember.department_id == OrgDepartment.id)
            .where(OrgMember.status == "active")
            .scalar_subquery()
        )
        
        await db.execute(
            update(OrgDepartment)
            .where(OrgDepartment.provider_id == provider_id)
            .where(OrgDepartment.status == "active")
            .values(member_count=direct_subquery)
        )

        # 2. Fetch all active departments to compute recursive aggregated counts
        result = await db.execute(
            select(OrgDepartment.id, OrgDepartment.parent_id, OrgDepartment.member_count)
            .where(OrgDepartment.provider_id == provider_id)
            .where(OrgDepartment.status == "active")
        )
        rows = result.all()
        
        # Build tree structure and lookup
        dept_map = {row.id: {"parent_id": row.parent_id, "direct": row.member_count, "total": 0, "children": []} for row in rows}
        root_ids = []
        for d_id, d_data in dept_map.items():
            parent_id = d_data["parent_id"]
            if parent_id and parent_id in dept_map:
                dept_map[parent_id]["children"].append(d_id)
            else:
                root_ids.append(d_id)
                
        # Recursive function to calculate total
        def compute_total(node_id):
            node = dept_map[node_id]
            total = node["direct"]
            for child_id in node["children"]:
                total += compute_total(child_id)
            node["total"] = total
            return total
            
        for root_id in root_ids:
            compute_total(root_id)
            
        # 3. Bulk update all departments with their aggregated total counts
        # Skip if no updates needed to avoid unnecessary writes, but usually it's fast enough
        update_mappings = [{"id": d_id, "member_count": d_data["total"]} for d_id, d_data in dept_map.items()]
        
        if update_mappings:
            from app.database import async_engine
            # Use core update with executemany approach handled cleanly by SQLAlchemy mapping
            # SQLAlchemy 2.0 style bulk update
            from sqlalchemy import bindparam
            stmt = (
                update(OrgDepartment)
                .where(OrgDepartment.id == bindparam("b_id"))
                .values(member_count=bindparam("b_count"))
            )
            # Re-map keys for bindparams
            bind_mappings = [{"b_id": m["id"], "b_count": m["member_count"]} for m in update_mappings]
            await db.execute(stmt, bind_mappings)

    async def _ensure_provider(self, db: AsyncSession) -> IdentityProvider:
        """Ensure IdentityProvider record exists."""
        if self.provider:
            return self.provider

        # If we have an ID, look it up
        if hasattr(self, 'provider_id') and self.provider_id:
            result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == self.provider_id))
            self.provider = result.scalar_one_or_none()
            if self.provider:
                return self.provider

        # Fallback by type (scoped by tenant)
        query = select(IdentityProvider).where(IdentityProvider.provider_type == self.provider_type)
        if self.tenant_id:
            query = query.where(IdentityProvider.tenant_id == self.tenant_id)
        else:
            query = query.where(IdentityProvider.tenant_id.is_(None))
            
        result = await db.execute(query)
        provider = result.scalars().first()

        if not provider:
            provider = IdentityProvider(
                provider_type=self.provider_type,
                name=self.provider_type.capitalize(),
                is_active=True,
                config=self.config,
                tenant_id=self.tenant_id
            )
            db.add(provider)
            await db.flush()

        self.provider = provider
        return provider

    async def _upsert_department(
        self, db: AsyncSession, provider: IdentityProvider, dept: ExternalDepartment
    ):
        """Insert or update a department."""
        # Check if exists by external_id and provider
        result = await db.execute(
            select(OrgDepartment).where(
                OrgDepartment.external_id == dept.external_id,
                OrgDepartment.provider_id == provider.id,
            )
        )
        existing = result.scalars().first()

        now = datetime.now()
        path = f"{dept.parent_external_id}/{dept.name}" if dept.parent_external_id else dept.name

        # Resolve parent_id from parent_external_id
        parent_id = None
        if dept.parent_external_id:
            parent_result = await db.execute(
                select(OrgDepartment).where(
                    OrgDepartment.external_id == dept.parent_external_id,
                    OrgDepartment.provider_id == provider.id,
                )
            )
            parent_dept = parent_result.scalars().first()
            if parent_dept:
                parent_id = parent_dept.id

        if existing:
            existing.name = dept.name
            existing.member_count = dept.member_count
            existing.path = path
            existing.external_id = dept.external_id
            existing.provider_id = provider.id
            existing.parent_id = parent_id
            existing.status = "active"
            existing.synced_at = now
        else:
            new_dept = OrgDepartment(
                external_id=dept.external_id,
                provider_id=provider.id,
                name=dept.name,
                parent_id=parent_id,
                path=path,
                member_count=dept.member_count,
                tenant_id=self.tenant_id,
                synced_at=now,
            )
            db.add(new_dept)

        await db.flush()

    async def _upsert_member(
        self,
        db: AsyncSession,
        provider: IdentityProvider,
        user: ExternalUser,
        department_external_id: str,
    ) -> dict[str, Any]:
        """Insert or update a member, platform user, and identity."""
        stats = {"user_created": False, "profile_synced": False}

        # Find department using user's actual department list.
        # DingTalk's dept_id_list last item is the most specific (leaf) department.
        # We prefer the last entry that exists in our local DB.
        department = None
        if user.department_ids:
            # Iterate in reverse so we try the most specific dept first
            for dept_ext_id in reversed(user.department_ids):
                dept_result = await db.execute(
                    select(OrgDepartment).where(
                        OrgDepartment.external_id == dept_ext_id,
                        OrgDepartment.provider_id == provider.id,
                    )
                )
                department = dept_result.scalars().first()
                if department:
                    break
        # Fallback: use the department_external_id that was set during fetch_users
        if not department and user.department_external_id:
            dept_result = await db.execute(
                select(OrgDepartment).where(
                    OrgDepartment.external_id == user.department_external_id,
                    OrgDepartment.provider_id == provider.id,
                )
            )
            department = dept_result.scalars().first()

        # Check if exists by external_id and provider
        result = await db.execute(
            select(OrgMember).where(
                OrgMember.external_id == user.external_id,
                OrgMember.provider_id == provider.id,
            )
        )
        existing_member = result.scalars().first()

        now = datetime.now()

        # Note: Platform user creation is disabled - just sync OrgMember
        # Users will be linked to platform users manually or via SSO login
        
        # Search for existing platform user by email/phone to associate with this member
        user_id = None
        platform_user = None
        email = _normalize_contact(user.email)
        mobile = _normalize_contact(user.mobile)

        if email:
            user_query = select(User).where(User.email.ilike(email))
            if self.tenant_id:
                user_query = user_query.where(User.tenant_id == self.tenant_id)
            user_res = await db.execute(user_query)
            platform_user = user_res.scalars().first()
            if platform_user:
                user_id = platform_user.id

        if not user_id and mobile:
            user_query = select(User).where(User.primary_mobile == mobile)
            if self.tenant_id:
                user_query = user_query.where(User.tenant_id == self.tenant_id)
            user_res = await db.execute(user_query)
            platform_user = user_res.scalars().first()
            if platform_user:
                user_id = platform_user.id

        # Update/Create OrgMember
        if existing_member:
            existing_member.name = user.name
            # Generate transliteration
            existing_member.name_translit_full = "".join([i[0] for i in pinyin(user.name, style=Style.NORMAL)])
            existing_member.name_translit_initial = "".join([i[0] for i in pinyin(user.name, style=Style.FIRST_LETTER)])
            
            if email is not None:
                existing_member.email = email
            existing_member.avatar_url = user.avatar_url
            existing_member.title = user.title
            existing_member.department_id = department.id if department else None
            existing_member.department_path = department.path if department else user.department_path
            if mobile is not None:
                existing_member.phone = mobile
            existing_member.status = user.status
            
            # Universal ID fields
            existing_member.external_id = user.external_id
            existing_member.open_id = user.open_id
            
            existing_member.provider_id = provider.id
            existing_member.synced_at = now
            if user_id and not existing_member.user_id:
                existing_member.user_id = user_id
            stats["profile_synced"] = True
        else:
            translit_full = "".join([i[0] for i in pinyin(user.name, style=Style.NORMAL)])
            translit_initial = "".join([i[0] for i in pinyin(user.name, style=Style.FIRST_LETTER)])
            
            new_member = OrgMember(
                external_id=user.external_id,
                open_id=user.open_id,

                provider_id=provider.id,
                user_id=user_id,
                name=user.name,
                name_translit_full=translit_full,
                name_translit_initial=translit_initial,
                email=email,
                avatar_url=user.avatar_url,
                title=user.title,
                department_id=department.id if department else None,
                department_path=department.path if department else user.department_path,
                phone=mobile,
                status=user.status,
                tenant_id=self.tenant_id,
                synced_at=now,
            )
            db.add(new_member)
            stats["profile_synced"] = True

        # Sync email/phone from OrgMember to User (if linked)
        target_user = platform_user
        if not target_user and (user_id or (existing_member and existing_member.user_id)):
            target_id = user_id or existing_member.user_id
            user_res = await db.execute(select(User).where(User.id == target_id))
            target_user = user_res.scalars().first()

        if target_user:
            if email and target_user.email != email:
                target_user.email = email
            if mobile and target_user.primary_mobile != mobile:
                target_user.primary_mobile = mobile

        await db.flush()
        return stats

    async def _resolve_platform_user(self, db: AsyncSession, user: ExternalUser) -> User | None:
        """Resolve platform user from external user info."""
        # 1. Try by Email matching (primary way now)
        email = _normalize_contact(user.email)
        if email:
            result = await db.execute(
                select(User).where(User.email.ilike(email))
            )
            u = result.scalars().first()
            if u: return u

        # 2. Try by mobile matching
        mobile = _normalize_contact(user.mobile)
        if mobile:
            result = await db.execute(
                select(User).where(User.primary_mobile == mobile)
            )
            u = result.scalars().first()
            if u: return u

        return None


class FeishuOrgSyncAdapter(BaseOrgSyncAdapter):
    """Feishu organization sync adapter."""

    provider_type = "feishu"

    FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    FEISHU_DEPT_URL = "https://open.feishu.cn/open-apis/contact/v3/departments"
    FEISHU_USERS_URL = "https://open.feishu.cn/open-apis/contact/v3/users/find_by_department"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None, tenant_id: uuid.UUID | None = None):
        super().__init__(provider, config, tenant_id)
        self.app_id = self.config.get("app_id")
        self.app_secret = self.config.get("app_secret")

    @property
    def api_base_url(self) -> str:
        return "https://open.feishu.cn/open-apis"

    async def get_access_token(self) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.FEISHU_APP_TOKEN_URL,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            return data.get("tenant_access_token") or data.get("app_access_token") or ""

    async def fetch_departments(self) -> list[ExternalDepartment]:
        """Fetch all departments from Feishu using concurrent recursive calls to get parent-child relationships."""
        import asyncio
        token = await self.get_access_token()
        all_depts: list[ExternalDepartment] = []
        # Add a virtual root for the tenant, consistent with DingTalk root behavior
        all_depts.append(
            ExternalDepartment(
                external_id="0",
                name="Root",
                parent_external_id=None,
                member_count=0,
                raw_data={"department_id": "0", "name": "Root"}
            )
        )
        
        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(15)  # Limit concurrent requests to avoid rate limits
            
            async def fetch_children(parent_id: str):
                page_token = ""
                tasks = []
                while True:
                    params = {
                        "department_id_type": "open_department_id",
                        "fetch_child": "false",
                        "page_size": "50",
                    }
                    if page_token:
                        params["page_token"] = page_token

                    async with sem:
                        resp = await client.get(
                            f"{self.FEISHU_DEPT_URL}/{parent_id}/children", 
                            params=params, 
                            headers={"Authorization": f"Bearer {token}"}
                        )
                    data = resp.json()

                    if data.get("code") != 0:
                        logger.error(f"Feishu fetch departments list error for parent {parent_id}: {data}")
                        break

                    res_data = data.get("data", {})
                    items = res_data.get("items", []) or []
                    for item in items:
                        dept_id = item.get("open_department_id")
                        if not dept_id: continue
                        
                        # Since we fetched using parent_id, we intrinsically know the parent!
                        parent_external = parent_id if parent_id and parent_id != "0" else "0"
                        
                        dept = ExternalDepartment(
                            external_id=dept_id,
                            name=item.get("name", ""),
                            parent_external_id=parent_external,
                            member_count=item.get("member_count", 0),
                            raw_data=item,
                        )
                        all_depts.append(dept)
                        
                        # Recursively fetch children for this department
                        tasks.append(fetch_children(dept_id))

                    page_token = res_data.get("page_token", "")
                    if not page_token:
                        break
                        
                if tasks:
                    await asyncio.gather(*tasks)

            await fetch_children("0")
                        
        logger.info(f"Feishu fetched {len(all_depts)} departments total.")
        return all_depts

    async def fetch_users(self, department_external_id: str) -> list[ExternalUser]:
        """Fetch users in a department.
        
        Uses user_id_type=user_id which requires the contact:user.employee_id:readonly
        permission. If the Feishu API returns an error due to missing permission, raises
        a clear error instructing the user to add the required scope.
        """
        token = await self.get_access_token()
        users: list[ExternalUser] = []
        page_token = ""

        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "department_id": department_external_id,
                    "department_id_type": "open_department_id",
                    "user_id_type": "user_id",  # Requires contact:user.employee_id:readonly
                    "page_size": "50",
                }
                if page_token:
                    params["page_token"] = page_token

                resp = await client.get(
                    self.FEISHU_USERS_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()

                if data.get("code") != 0:
                    error_code = data.get("code")
                    error_msg = data.get("msg", "")
                    logger.error(
                        f"Feishu fetch users error for dept {department_external_id}: "
                        f"code={error_code}, msg={error_msg}"
                    )
                    # Raise a user-friendly error for permission issues
                    raise RuntimeError(
                        f"Feishu API error (code {error_code}): {error_msg}. "
                        f"Please ensure the Feishu app has the 'contact:user.employee_id:readonly' "
                        f"permission enabled. Go to Feishu Open Platform -> App -> Permissions -> "
                        f"search 'employee_id' -> enable and publish a new version."
                    )

                res_data = data.get("data", {})
                items = res_data.get("items", []) or []
                for item in items:
                    # Collect all departments the user belongs to
                    raw_dept_ids = item.get("department_ids", [])
                    department_ids = [str(did) for did in raw_dept_ids] if raw_dept_ids else [department_external_id]
                    
                    external_id = item.get("user_id", "") or item.get("open_id", "")
                    
                    # For Feishu, a user is considered inactive if they are explicitly frozen or resigned.
                    # Merely not being activated (is_activated=False) shouldn't hide them from the org chart.
                    feishu_status = item.get("status", {})
                    is_frozen = feishu_status.get("is_frozen", False)
                    is_resigned = feishu_status.get("is_resigned", False)
                    member_status = "inactive" if (is_frozen or is_resigned) else "active"

                    user = ExternalUser(
                        external_id=external_id,
                        open_id=item.get("open_id", ""),
                        unionid=item.get("union_id", ""),
                        name=item.get("name", ""),
                        email=item.get("email", ""),
                        avatar_url=item.get("avatar_url", ""),
                        title=item.get("title", ""),
                        department_external_id=department_external_id,
                        department_ids=department_ids,
                        mobile=item.get("mobile", ""),
                        status=member_status,
                        raw_data=item,
                    )
                    users.append(user)

                page_token = res_data.get("page_token", "")
                if not page_token:
                    break

        return users


class DingTalkOrgSyncAdapter(BaseOrgSyncAdapter):
    """DingTalk organization sync adapter."""

    provider_type = "dingtalk"

    DINGTALK_API_URL = "https://oapi.dingtalk.com"
    DINGTALK_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
    DINGTALK_DEPT_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/department/listsub"
    DINGTALK_USER_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/user/list"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None, tenant_id: uuid.UUID | None = None):
        super().__init__(provider, config, tenant_id)
        self.app_key = self.config.get("app_key") or self.config.get("appkey") or self.config.get("app_id")
        self.app_secret = self.config.get("app_secret") or self.config.get("appsecret") or self.config.get("app_secret_key")
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._dept_path_map: dict[str, str] = {}

    @property
    def api_base_url(self) -> str:
        return self.DINGTALK_API_URL

    async def get_access_token(self) -> str:
        """Get or refresh the DingTalk access token.

        Cached in Redis (preferred) with in-memory fallback.
        Key: clawith:token:dingtalk_corp:{app_key}
        TTL: expires_in - 300s (5 min early refresh)
        """
        from app.core.token_cache import get_cached_token, set_cached_token

        if not self.app_key or not self.app_secret:
            raise ValueError("DingTalk app_key/app_secret missing in provider config")

        cache_key = f"clawith:token:dingtalk_corp:{self.app_key}"
        cached = await get_cached_token(cache_key)
        if cached:
            self._access_token = cached
            return cached

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.DINGTALK_TOKEN_URL,
                params={"appkey": self.app_key, "appsecret": self.app_secret},
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"DingTalk token error: {data.get('errmsg') or data}")
            token = data.get("access_token") or ""
            expires_in = int(data.get("expires_in") or 7200)
            if token:
                ttl = max(expires_in - 300, 60)
                await set_cached_token(cache_key, token, ttl)
                self._access_token = token
                self._token_expires_at = datetime.now() + timedelta(seconds=max(expires_in - 60, 60))
            return token

    async def fetch_departments(self) -> list[ExternalDepartment]:
        token = await self.get_access_token()
        all_depts: list[ExternalDepartment] = []
        # dept_index: external_id -> (name, parent_external_id_str | None)
        dept_index: dict[str, tuple[str, str | None]] = {}

        seen: set[int] = set()
        queue: list[int] = [1]  # DingTalk root dept id

        async with httpx.AsyncClient() as client:
            while queue:
                parent_id = queue.pop(0)
                if parent_id in seen:
                    continue
                seen.add(parent_id)

                resp = await client.post(
                    self.DINGTALK_DEPT_LIST_URL,
                    params={"access_token": token},
                    json={"dept_id": parent_id},
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    raise RuntimeError(f"DingTalk department list error: {data.get('errmsg') or data}")

                result = data.get("result")
                if isinstance(result, list):
                    items = result
                elif isinstance(result, dict):
                    items = result.get("department", []) or []
                else:
                    items = []

                for item in items:
                    dept_id = int(item.get("dept_id"))
                    dept_name = item.get("name", "")
                    # Use actual parent_id from API response to preserve real hierarchy
                    raw_parent_id = item.get("parent_id")
                    if dept_id == 1 or not raw_parent_id or int(raw_parent_id) == dept_id:
                        parent_external = None  # Root has no parent
                    else:
                        parent_external = str(int(raw_parent_id))
                    external_id = str(dept_id)
                    dept_index[external_id] = (dept_name, parent_external)
                    all_depts.append(
                        ExternalDepartment(
                            external_id=external_id,
                            name=dept_name,
                            parent_external_id=parent_external,
                            member_count=item.get("member_count", 0) or 0,
                            raw_data=item,
                        )
                    )
                    if dept_id not in seen:
                        queue.append(dept_id)

        # Ensure root exists in index (for path building and possible member sync)
        if "1" not in dept_index:
            dept_index["1"] = ("Root", None)
            all_depts.append(ExternalDepartment(external_id="1", name="Root", parent_external_id=None, member_count=0, raw_data={"dept_id": 1, "name": "Root"}))

        self._dept_path_map = self._build_dept_paths(dept_index)
        return all_depts

    async def fetch_users(self, department_external_id: str) -> list[ExternalUser]:
        token = await self.get_access_token()
        users: list[ExternalUser] = []
        cursor = 0
        dept_id = int(department_external_id)
        dept_path = self._dept_path_map.get(department_external_id, "")

        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.post(
                    self.DINGTALK_USER_LIST_URL,
                    params={"access_token": token},
                    json={"dept_id": dept_id, "cursor": cursor, "size": 100},
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    raise RuntimeError(f"DingTalk user list error: {data.get('errmsg') or data}")

                result = data.get("result", {}) or {}
                items = result.get("list", []) or []
                for item in items:
                    external_id = item.get("userid") or item.get("user_id") or ""
                    # Get user's actual department list from DingTalk data
                    dept_id_list = item.get("dept_id_list", [])
                    department_ids = [str(did) for did in dept_id_list] if dept_id_list else [department_external_id]
                    # Use last level department (last item in list is most specific)
                    last_dept_id = department_ids[-1] if department_ids else department_external_id
                    last_dept_path = self._dept_path_map.get(last_dept_id, "")
                    user = ExternalUser(
                        external_id=external_id,
                        unionid=item.get("unionid", "") or "",
                        open_id=item.get("openid", "") or "",
                        name=item.get("name", ""),
                        email=item.get("email", "") or "",
                        avatar_url=item.get("avatar", "") or "",
                        title=item.get("title", "") or "",
                        department_external_id=last_dept_id,
                        department_path=last_dept_path,
                        department_ids=department_ids,
                        mobile=item.get("mobile", "") or "",
                        status="active" if item.get("active", True) else "inactive",
                        raw_data=item,
                    )
                    users.append(user)

                if not result.get("has_more"):
                    break
                cursor = int(result.get("next_cursor") or 0)

        return users

    def _build_dept_paths(self, dept_index: dict[str, tuple[str, str | None]]) -> dict[str, str]:
        paths: dict[str, str] = {}

        def compute_path(dept_id: str, visited: set[str] | None = None) -> str:
            if dept_id in paths:
                return paths[dept_id]
            if visited is None:
                visited = set()
            if dept_id in visited:
                # Cycle guard
                paths[dept_id] = dept_id
                return dept_id
            visited.add(dept_id)
            name, parent_id = dept_index.get(dept_id, ("", None))
            if not parent_id or parent_id not in dept_index:
                paths[dept_id] = name
                return name
            parent_path = compute_path(parent_id, visited)
            full = f"{parent_path}/{name}" if parent_path else name
            paths[dept_id] = full
            return full

        for did in list(dept_index.keys()):
            compute_path(did)
        return paths


class WeComOrgSyncAdapter(BaseOrgSyncAdapter):
    """WeCom organization sync adapter."""

    provider_type = "wecom"

    WECOM_API_URL = "https://qyapi.weixin.qq.com"
    WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    WECOM_DEPT_LIST_URL = "https://qyapi.weixin.qq.com/cgi-bin/department/list"
    WECOM_USER_LIST_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/list"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None, tenant_id: uuid.UUID | None = None):
        super().__init__(provider, config, tenant_id)
        # Handle various config key naming conventions
        self.corp_id = self.config.get("corp_id") or self.config.get("app_id") or self.config.get("corpid")
        self.secret = self.config.get("secret") or self.config.get("app_secret") or self.config.get("corpsecret") or self.config.get("bot_secret")
        self.bot_id = self.config.get("bot_id")
        self.bot_secret = self.config.get("bot_secret") or self.secret
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    @property
    def api_base_url(self) -> str:
        return self.WECOM_API_URL

    async def get_access_token(self) -> str:
        """Get valid access token for WeCom API.

        Cached in Redis (preferred) with in-memory fallback.
        Key: clawith:token:wecom:{corp_id}
        TTL: expires_in - 300s (5 min early refresh)
        """
        from app.core.token_cache import get_cached_token, set_cached_token

        # Determine identifier for cache key
        _corp_key = self.corp_id or self.bot_id or ""
        cache_key = f"clawith:token:wecom:{_corp_key}"

        if _corp_key:
            cached = await get_cached_token(cache_key)
            if cached:
                self._access_token = cached
                return cached

        # Priority 1: Standard CorpID + Secret
        if self.corp_id and self.secret:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self.WECOM_TOKEN_URL,
                    params={"corpid": self.corp_id, "corpsecret": self.secret},
                )
                data = resp.json()
                if data.get("errcode") == 0:
                    token = data.get("access_token") or ""
                    expires_in = int(data.get("expires_in") or 7200)
                    if token:
                        ttl = max(expires_in - 300, 300)
                        await set_cached_token(cache_key, token, ttl)
                        self._access_token = token
                        self._token_expires_at = datetime.now() + timedelta(seconds=ttl)
                    return token
                else:
                    logger.error(f"[WeCom Sync] Token error with corp_id: {data}")

        # Priority 2: Try bot_id as corp_id if no corp_id provided (fallback)
        if not self.corp_id and self.bot_id and self.bot_secret:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self.WECOM_TOKEN_URL,
                    params={"corpid": self.bot_id, "corpsecret": self.bot_secret},
                )
                data = resp.json()
                if data.get("errcode") == 0:
                    token = data.get("access_token") or ""
                    expires_in = int(data.get("expires_in") or 7200)
                    if token:
                        ttl = max(expires_in - 300, 300)
                        await set_cached_token(cache_key, token, ttl)
                        self._access_token = token
                        self._token_expires_at = datetime.now() + timedelta(seconds=ttl)
                    return token

        raise ValueError("WeCom credentials (corp_id/secret or bot_id/secret) missing or invalid")

    async def fetch_departments(self) -> list[ExternalDepartment]:
        """Fetch all departments from WeCom."""
        token = await self.get_access_token()
        all_depts: list[ExternalDepartment] = []

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self.WECOM_DEPT_LIST_URL,
                params={"access_token": token},
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"WeCom department list error: {data.get('errmsg') or data}")

            items = data.get("department", [])
            for item in items:
                dept_id = str(item.get("id"))
                parent_id = str(item.get("parentid")) if item.get("parentid") and item.get("parentid") != 0 else None
                
                all_depts.append(
                    ExternalDepartment(
                        external_id=dept_id,
                        name=item.get("name", ""),
                        parent_external_id=parent_id,
                        member_count=0,  # WeCom doesn't return member count in this API
                        raw_data=item,
                    )
                )
        return all_depts

    async def fetch_users(self, department_external_id: str) -> list[ExternalUser]:
        """Fetch user details in a department from WeCom."""
        token = await self.get_access_token()
        users: list[ExternalUser] = []

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self.WECOM_USER_LIST_URL,
                params={
                    "access_token": token,
                    "department_id": department_external_id,
                    "fetch_child": 0,  # Only this department, parent loop handles recursion
                },
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"WeCom user list error: {data.get('errmsg') or data}")

            items = data.get("userlist", [])
            for item in items:
                external_id = item.get("userid", "")
                dept_ids = [str(did) for did in item.get("department", [])]
                
                user = ExternalUser(
                    external_id=external_id,
                    name=item.get("name", ""),
                    open_id="",  # WeCom doesn't return openid in list API
                    email=item.get("email", "") or item.get("biz_mail", ""),
                    avatar_url=item.get("avatar", ""),
                    title=item.get("position", ""),
                    department_external_id=department_external_id,
                    department_ids=dept_ids,
                    mobile=item.get("mobile", ""),
                    status="active" if item.get("status") == 1 else "inactive",
                    raw_data=item,
                )
                users.append(user)

        return users


# Adapter class mapping
SYNC_ADAPTER_CLASSES = {
    "feishu": FeishuOrgSyncAdapter,
    "dingtalk": DingTalkOrgSyncAdapter,
    "wecom": WeComOrgSyncAdapter,
}


async def get_org_sync_adapter(
    db: AsyncSession,
    provider_type: str,
    tenant_id: uuid.UUID | None = None,
    provider_id: uuid.UUID | None = None,
) -> BaseOrgSyncAdapter | None:
    """Factory function to create org sync adapter.

    Args:
        db: Database session
        provider_type: Type of provider (feishu, dingtalk, etc.)
        tenant_id: Optional tenant ID
        provider_id: Optional specific provider ID (if not provided, uses first found by type)

    Returns:
        Adapter instance or None if not supported
    """
    # Get provider config from database - prefer specific provider_id if provided
    if provider_id:
        result = await db.execute(
            select(IdentityProvider).where(IdentityProvider.id == provider_id)
        )
    else:
        query = select(IdentityProvider).where(IdentityProvider.provider_type == provider_type)
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)
        else:
            query = query.where(IdentityProvider.tenant_id.is_(None))
        result = await db.execute(query)
    provider = result.scalar_one_or_none()

    adapter_class = SYNC_ADAPTER_CLASSES.get(provider_type)
    if not adapter_class:
        return None

    config = provider.config if provider else {}
    return adapter_class(provider=provider, config=config, tenant_id=tenant_id)
