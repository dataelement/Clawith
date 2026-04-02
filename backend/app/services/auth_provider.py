"""Generic OAuth/SSO authentication provider framework.

This module provides a base class for all identity providers (Feishu, DingTalk, WeCom, etc.)
and concrete implementations for each supported provider.
"""

import httpx
from abc import ABC, abstractmethod
from fastapi import HTTPException
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.models.identity import IdentityProvider
from app.models.user import User, Identity
from loguru import logger


@dataclass
class ExternalUserInfo:
    """Standardized user info from external identity providers."""

    provider_type: str
    provider_user_id: str
    provider_union_id: str | None = None
    name: str = ""
    email: str = ""
    avatar_url: str = ""
    mobile: str = ""
    raw_data: dict = None

    def __post_init__(self):
        if self.raw_data is None:
            self.raw_data = {}


class BaseAuthProvider(ABC):
    """Abstract base class for all authentication providers."""

    provider_type: str = ""

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None):
        """Initialize provider with optional config from database.

        Args:
            provider: IdentityProvider model instance from database
            config: Configuration dict (fallback if no provider record)
        """
        self.provider = provider
        self.config = config or {}
        if provider and provider.config:
            self.config = provider.config

    @abstractmethod
    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Generate OAuth authorization URL.

        Args:
            redirect_uri: Callback URL after authorization
            state: CSRF state parameter

        Returns:
            Authorization URL to redirect user to
        """
        pass

    @abstractmethod
    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            Dict containing access_token and optionally refresh_token
        """
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        """Fetch user profile from provider API.

        Args:
            access_token: Valid access token

        Returns:
            ExternalUserInfo instance with user data
        """
        pass

    async def find_or_create_user(
        self, db: AsyncSession, user_info: ExternalUserInfo, tenant_id: str | None = None
    ) -> tuple[User, bool]:
        """Find existing user or create new one via Identity/OrgMember.

        Args:
            db: Database session
            user_info: User info from provider
            tenant_id: Optional tenant ID for association
        """
        from app.services.sso_service import sso_service
        from sqlalchemy.orm import selectinload

        # Ensure provider exists
        await self._ensure_provider(db, tenant_id)

        # 1. Try lookup via sso_service (which now uses OrgMember)
        provider_user_id = user_info.provider_union_id or user_info.provider_user_id
        user = await sso_service.resolve_user_identity(
            db, provider_user_id, self.provider_type, tenant_id=tenant_id
        )
        
        # Feishu: fallback to open_id if union_id lookup misses
        if (
            not user
            and self.provider_type == "feishu"
            and user_info.provider_union_id
            and user_info.provider_user_id
        ):
            user = await sso_service.resolve_user_identity(
                db, user_info.provider_user_id, self.provider_type, tenant_id=tenant_id
            )

        is_new = False
        if not user:
            # 2. Try matching by email/mobile (which now checks Identity too)
            if user_info.email:
                user = await sso_service.match_user_by_email(db, user_info.email, tenant_id)
            if not user and user_info.mobile:
                user = await sso_service.match_user_by_mobile(db, user_info.mobile, tenant_id)
            
            if user:
                # If we found a user via email/mobile matching, it might be in a different tenant
                if tenant_id and str(user.tenant_id) != tenant_id:
                    # Identity exists but no user in this tenant
                    user = None 

        # 5. 通过 provider_user_id 匹配现有用户 username（同租户下唯一登录凭证）
        if not user and user_info.provider_user_id:
            from sqlalchemy import and_
            from app.models.user import Identity as IdentityModel
            from sqlalchemy.orm import selectinload

            # 构建查询条件：tenant_id 有值时匹配该租户或无租户的用户，为 None 时不限制租户
            if tenant_id:
                from sqlalchemy import or_
                where_clause = and_(
                    IdentityModel.username == user_info.provider_user_id,
                    or_(User.tenant_id == tenant_id, User.tenant_id.is_(None)),
                )
            else:
                where_clause = IdentityModel.username == user_info.provider_user_id

            result = await db.execute(
                select(User).join(User.identity).where(where_clause).options(selectinload(User.identity))
            )
            candidate = result.scalar_one_or_none()
            if candidate:
                user = candidate
                await sso_service.link_identity(
                    db,
                    str(user.id),
                    self.provider_type,
                    user_info.provider_user_id,
                    user_info.raw_data,
                    tenant_id=tenant_id,
                )
                logger.info(f"[SSO] Matched existing user by username: {user.username} (tenant_id={tenant_id})")


        # 匹配到用户后，检查是否需要自动绑定租户
        if user and tenant_id and not user.tenant_id:
            user.tenant_id = tenant_id
            logger.info(f"[SSO] Auto-bound user {user.username} to tenant {tenant_id}")

        if user:
            # Update user info and ensure identity is loaded
            if not user.identity_id:
                 from app.services.registration_service import registration_service
                 identity = await registration_service.find_or_create_identity(db, email=user_info.email, phone=user_info.mobile)
                 user.identity_id = identity.id

            # Ensure identity is loaded for proxy field access
            if not hasattr(user, "identity") or user.identity is None:
                from sqlalchemy.orm import selectinload as _sil
                refreshed = await db.execute(
                    select(User).where(User.id == user.id).options(_sil(User.identity))
                )
                user = refreshed.scalar_one()

            await self._update_existing_user(db, user, user_info)
        else:
            # 3. Create new user (and Identity if needed)
            user = await self._create_new_user(db, user_info, tenant_id)
            is_new = True
            
        # Ensure OrgMember linkage
        await sso_service.link_identity(
            db,
            str(user.id),
            self.provider_type,
            provider_user_id,
            user_info.raw_data,
            tenant_id=tenant_id,
        )

        return user, is_new

    async def _ensure_provider(self, db: AsyncSession, tenant_id: str | None = None) -> IdentityProvider:
        """Get or create IdentityProvider record."""
        if self.provider:
            return self.provider

        query = select(IdentityProvider).where(IdentityProvider.provider_type == self.provider_type)
        if tenant_id:
            query = query.where(IdentityProvider.tenant_id == tenant_id)
            
        result = await db.execute(query)
        provider = result.scalar_one_or_none()

        if not provider:
            provider = IdentityProvider(
                provider_type=self.provider_type,
                name=self.provider_type.capitalize(),
                is_active=True,
                config=self.config,
                tenant_id=tenant_id,
            )
            db.add(provider)
            await db.flush()

        self.provider = provider
        return provider

    async def _find_user_by_legacy_fields(self, db: AsyncSession, user_info: ExternalUserInfo) -> User | None:
        """Find user by legacy provider-specific fields (if any)."""
        return None  # Override in subclasses for backward compatibility

    async def _update_existing_user(
        self, db: AsyncSession, user: User, user_info: ExternalUserInfo
    ):
        """Update existing user with new info from provider."""
        if user_info.name and not user.display_name:
            user.display_name = user_info.name
        if user_info.avatar_url and not user.avatar_url:
            user.avatar_url = user_info.avatar_url

        # Update identity fields (email/mobile/username) through the identity relationship
        identity = user.identity
        if identity:
            if user_info.email and not identity.email:
                identity.email = user_info.email
            if user_info.mobile and not identity.phone:
                identity.phone = user_info.mobile
            # OAuth2 登录时用 provider_user_id 更新 dingtalk_ 开头的临时用户名
            if (
                user_info.provider_user_id
                and identity.username
                and identity.username.startswith("dingtalk_")
                and self.provider_type == "oauth2"
            ):
                old_username = identity.username
                identity.username = user_info.provider_user_id
                logger.info(f"[SSO] Updated username from {old_username} to {user_info.provider_user_id}")

        # Update legacy fields if applicable
        await self._update_legacy_user_fields(user, user_info)

    async def _create_new_user(
        self, db: AsyncSession, user_info: ExternalUserInfo, tenant_id: str | None
    ) -> User:
        """Create new user from external identity."""
        from app.services.registration_service import registration_service
        import uuid
        
        # 1. Prepare user fields and resolve global identity
        effective_id = user_info.provider_user_id or user_info.provider_union_id or "unknown"
        
        identity = await registration_service.find_or_create_identity(
            db,
            email=user_info.email,
            phone=user_info.mobile,
            username=user_info.email.split("@")[0] if user_info.email else None,
            password=effective_id,
        )

        # 2. Prepare Tenant user fields
        username = user_info.email.split("@")[0] if user_info.email else f"{self.provider_type}_{effective_id[:8]}"

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
            username = f"{username}_{uuid.uuid4().hex[:6]}"

        # 3. Create TenantUser record
        user = User(
            identity_id=identity.id,
            display_name=user_info.name or username,
            avatar_url=user_info.avatar_url,
            registration_source=self.provider_type,
            tenant_id=tenant_id,
            is_active=True,
        )

        # Set legacy fields if needed
        await self._set_legacy_user_fields(user, user_info)

        db.add(user)
        await db.flush()

        # Preload identity for downstream access
        user.identity = identity
        return user

    async def _update_legacy_user_fields(self, user: User, user_info: ExternalUserInfo):
        """Override in subclass to update provider-specific legacy fields."""
        pass

    async def _set_legacy_user_fields(self, user: User, user_info: ExternalUserInfo):
        """Override in subclass to set provider-specific legacy fields on new user."""
        pass


class FeishuAuthProvider(BaseAuthProvider):
    """Feishu (Lark) OAuth provider implementation."""

    provider_type = "feishu"

    FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
    FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
    FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None):
        super().__init__(provider, config)
        self.app_id = self.config.get("app_id")
        self.app_secret = self.config.get("app_secret")
        self._app_access_token: str | None = None

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        app_id = self.app_id or ""
        base_url = "https://open.feishu.cn/open-apis/authen/v1/authorize"
        params = f"app_id={app_id}&redirect_uri={redirect_uri}&state={state}"
        return f"{base_url}?{params}"

    async def get_app_access_token(self) -> str:
        """Get or refresh the Feishu app access token.

        Cached in Redis (preferred) with in-memory fallback.
        Key: clawith:token:feishu_tenant:{app_id}
        TTL: 6900s (7200s validity - 5 min early refresh)
        """
        from app.core.token_cache import get_cached_token, set_cached_token

        cache_key = f"clawith:token:feishu_tenant:{self.app_id}"
        cached = await get_cached_token(cache_key)
        if cached:
            self._app_access_token = cached
            return cached

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.FEISHU_APP_TOKEN_URL,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            token = data.get("app_access_token", "") or data.get("tenant_access_token", "")
            expire = data.get("expire", 7200)
            if token:
                ttl = max(expire - 300, 60)
                await set_cached_token(cache_key, token, ttl)
            self._app_access_token = token
            return token

    async def exchange_code_for_token(self, code: str) -> dict:
        app_token = await self.get_app_access_token()

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                self.FEISHU_TOKEN_URL,
                json={"grant_type": "authorization_code", "code": code},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            token_data = token_resp.json()
            return token_data.get("data", {})

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        async with httpx.AsyncClient() as client:
            info_resp = await client.get(
                self.FEISHU_USER_INFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            info_data = info_resp.json().get("data", {})
            logger.info(f"Feishu user info: {info_data}")

            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id=info_data.get("open_id", ""),
                provider_union_id=info_data.get("union_id"),
                name=info_data.get("name", ""),
                email=info_data.get("email", ""),
                avatar_url=info_data.get("avatar_url", ""),
                raw_data=info_data,
            )

    async def _find_user_by_legacy_fields(self, db: AsyncSession, user_info: ExternalUserInfo) -> User | None:
        """Feishu legacy lookup removed (open_id/union_id no longer stored on User)."""
        return None

    async def _update_legacy_user_fields(self, user: User, user_info: ExternalUserInfo):
        """No-op: legacy Feishu fields removed from User."""
        return

    async def _set_legacy_user_fields(self, user: User, user_info: ExternalUserInfo):
        """No-op: legacy Feishu fields removed from User."""
        return


class DingTalkAuthProvider(BaseAuthProvider):
    """DingTalk OAuth provider implementation."""

    provider_type = "dingtalk"

    DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
    DINGTALK_USER_INFO_URL = "https://api.dingtalk.com/v1.0/contact/users/me"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None):
        super().__init__(provider, config)
        self.app_key = self.config.get("app_key")
        self.app_secret = self.config.get("app_secret")
        self.corp_id = self.config.get("corp_id")

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        app_id = self.app_key or ""
        base_url = "https://login.dingtalk.com/oauth2/auth"
        from urllib.parse import quote
        # contact.user.email and contact.user.mobile require specific permissions in DingTalk console
        scope = "openid corpid fieldEmail contact.user.mobile"
        params = (
            f"corpId={self.corp_id}&client_id={app_id}&redirect_uri={quote(redirect_uri)}&"
            f"state={state}&response_type=code&scope={quote(scope)}&prompt=consent"
        )
        return f"{base_url}?{params}"

    async def exchange_code_for_token(self, code: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.DINGTALK_TOKEN_URL,
                json={
                    "clientId": self.app_key,
                    "clientSecret": self.app_secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            resp_data = resp.json()
            if resp.status_code != 200:
                logger.error(f"DingTalk token exchange failed (HTTP {resp.status_code}): {resp_data}")
                return {}

            # New DingTalk OAuth2 returns flat JSON with camelCase fields
            return {
                "access_token": resp_data.get("accessToken"),
                "refresh_token": resp_data.get("refreshToken"),
                "expires_in": resp_data.get("expireIn"),
            }

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        async with httpx.AsyncClient() as client:
            headers = {"x-acs-dingtalk-access-token": access_token}
            info_resp = await client.get(self.DINGTALK_USER_INFO_URL, headers=headers)
            info_data = info_resp.json()
            if info_resp.status_code != 200:
                logger.error(f"DingTalk user info fetch failed (HTTP {info_resp.status_code}): {info_data}")
                raise Exception(f"Failed to fetch user info: {info_data.get('message', 'Unknown error')}")

            # DingTalk new OAuth2 returns openId, unionId, nick, avatarUrl, mobile, email
            logger.info(f"DingTalk user info: {info_data}")
            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id=info_data.get("openId", ""),
                provider_union_id=info_data.get("unionId"),
                name=info_data.get("nick", ""),
                email=info_data.get("email", ""),
                avatar_url=info_data.get("avatarUrl", ""),
                mobile=info_data.get("mobile", ""),
                raw_data=info_data,
            )


class WeComAuthProvider(BaseAuthProvider):
    """WeCom (Enterprise WeChat) OAuth provider implementation."""

    provider_type = "wecom"

    WECOM_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
    WECOM_USER_INFO_URL = "https://api.weixin.qq.com/cgi-bin/user/getuserinfo"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None):
        super().__init__(provider, config)
        self.corp_id = self.config.get("corp_id")
        self.secret = self.config.get("secret")
        self.agent_id = self.config.get("agent_id")

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        base_url = "https://open.work.weixin.qq.com/wwlogin/sso/login"
        params = f"loginType=CorpPinCorp&appid={self.corp_id}&agentid={self.agent_id}&redirect_uri={redirect_uri}&state={state}"
        return f"{base_url}?{params}"

    async def exchange_code_for_token(self, code: str) -> dict:
        # WeCom uses different auth flow - get access token first
        async with httpx.AsyncClient() as client:
            token_resp = await client.get(
                self.WECOM_TOKEN_URL,
                params={
                    "corpid": self.corp_id,
                    "corpsecret": self.secret,
                },
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            if not access_token:
                logger.error(f"WeCom token error: {token_data}")
                return {}

            # Get user info with code
            user_resp = await client.get(
                self.WECOM_USER_INFO_URL,
                params={"access_token": access_token, "code": code},
            )
            user_data = user_resp.json()
            userid = user_data.get("UserId")
            if not userid:
                logger.error(f"WeCom user auth info missing UserId: {user_data}")
                return {}

            # Fetch detailed user info
            detail_res = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                params={"access_token": access_token, "userid": userid},
            )
            detail_data = detail_res.json()
            if detail_data.get("errcode") == 0:
                logger.info(f"WeCom user detail fetched for userid {userid}")
            else:
                logger.warning(f"WeCom user detail fetch failed: {detail_data}")

            # Pack all info into the access_token string to satisfy BaseAuthProvider interface
            import json
            packed_token = json.dumps({
                "access_token": access_token,
                "userid": userid,
                "detail": detail_data
            })
            
            return {"access_token": packed_token}

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        import json
        try:
            data = json.loads(access_token)
            userid = data.get("userid", "")
            detail = data.get("detail", {})
            
            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id=userid,
                name=detail.get("name") or f"WeCom {userid}",
                email=detail.get("email") or detail.get("biz_mail") or "",
                avatar_url=detail.get("avatar") or "",
                mobile=detail.get("mobile") or "",
                raw_data=detail,
            )
        except Exception as e:
            logger.error(f"WeCom get_user_info error: {e}")
            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id="",
                name="",
                raw_data={"error": str(e)},
            )



class OAuth2AuthProvider(BaseAuthProvider):
    """Generic OAuth2 provider implementation (RFC 6749 Authorization Code flow)."""

    provider_type = "oauth2"

    def __init__(self, provider=None, config=None):
        super().__init__(provider, config)
        self.client_id = self.config.get("client_id") or self.config.get("app_id", "")
        self.client_secret = self.config.get("client_secret") or self.config.get("app_secret", "")
        self.authorize_url = self.config.get("authorize_url", "")
        self.scope = self.config.get("scope", "")
        
        # 自动推导 token_url 和 user_info_url（如果为空）
        base = self.authorize_url.rsplit("/", 1)[0] if self.authorize_url else ""
        self.token_url = self.config.get("token_url") or f"{base}/token"
        self.user_info_url = self.config.get("user_info_url") or f"{base}/userinfo"

        # 字段映射配置（用户自定义）
        self.field_mapping = self.config.get("field_mapping", {})

        # 标准 OIDC 字段 fallback 顺序
        self.FIELD_DEFAULTS = {
            "user_id": ["sub", "userId", "id"],
            "name": ["name", "userName", "preferred_username", "nickname"],
            "email": ["email"],
            "mobile": ["phone_number", "mobile", "phone"],
            "avatar": ["picture", "avatar_url", "avatar"],
        }

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        from urllib.parse import quote
        params = (
            f"response_type=code"
            f"&client_id={quote(self.client_id)}"
            f"&redirect_uri={quote(redirect_uri)}"
            f"&scope={quote(self.scope)}"
            f"&state={state}"
        )
        return f"{self.authorize_url}?{params}"

    async def exchange_code_for_token(self, code: str) -> dict:
        import base64
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                headers={
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                },
            )
            if resp.status_code != 200:
                logger.error(f"OAuth2 token exchange failed (HTTP {resp.status_code}): {resp.text}")
                return {}
            return resp.json()

    def _get_field(self, data: dict, field_key: str) -> str:
        """Get a field value using user-defined mapping first, then standard OIDC fallbacks."""
        # 1. 优先用用户配置的映射字段
        custom_key = self.field_mapping.get(field_key)
        if custom_key and data.get(custom_key):
            return str(data[custom_key])
        # 2. 依次尝试标准 fallback 字段
        for std_key in self.FIELD_DEFAULTS.get(field_key, []):
            if data.get(std_key):
                return str(data[std_key])
        return ""

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.user_info_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp_data = resp.json()
            
            # 特殊格式: {"status": 0, "data": {...}}
            # 标准 OIDC 格式: 直接返回 flat object
            if "data" in resp_data and isinstance(resp_data["data"], dict):
                info = resp_data["data"]
            else:
                info = resp_data
            
            logger.info(f"OAuth2 user info: {info}")
            
            # 通用字段解析（优先用户自定义映射，再 fallback 到标准字段）
            user_id = self._get_field(info, "user_id")
            name = self._get_field(info, "name")
            email = self._get_field(info, "email")
            mobile = self._get_field(info, "mobile")
            
            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id=str(user_id),
                name=name,
                email=email,
                mobile=mobile,
                raw_data=info,
            )

    async def get_user_info_from_token_data(self, token_data: dict) -> ExternalUserInfo:
        """Extract user info from token exchange response (fallback when userinfo endpoint fails)."""
        info = token_data.copy()
        if "userInfo" in info and isinstance(info["userInfo"], dict):
            info = {**info, **info["userInfo"]}
        logger.info(f"OAuth2 user info from token_data: {info}")
        user_id = self._get_field(info, "user_id") or info.get("openid", "")
        name = self._get_field(info, "name")
        email = self._get_field(info, "email")
        mobile = self._get_field(info, "mobile")
        return ExternalUserInfo(
            provider_type=self.provider_type,
            provider_user_id=str(user_id),
            name=name,
            email=email,
            mobile=mobile,
            raw_data=info,
        )

    async def _create_new_user(self, db, user_info, tenant_id):
        """Override to use provider_user_id as username for OAuth2."""
        from app.services.registration_service import registration_service
        from app.models.user import Identity as IdentityModel

        # 优先用 provider_user_id（如 userId="zhangsan"），再用 email 前缀，最后 fallback
        username = (
            user_info.provider_user_id
            or (user_info.email.split("@")[0] if user_info.email else None)
            or f"oauth2_user"
        )

        # Check username uniqueness via Identity
        existing = await db.execute(
            select(User).join(User.identity).where(
                Identity.username == username,
            )
        )
        if existing.scalar_one_or_none():
            logger.error(
                f"[OAuth2] Username conflict detected: {username} already exists in tenant {tenant_id}. "
                f"This should not happen if find_or_create_user match chain is working correctly."
            )
            raise HTTPException(
                status_code=409,
                detail=f"Username '{username}' already exists. Please contact administrator."
            )

        email = user_info.email or f"{username}@oauth2.local"

        # Create or find Identity
        identity = await registration_service.find_or_create_identity(
            db,
            email=email,
            phone=user_info.mobile,
            username=username,
            password=user_info.provider_user_id or username,
        )

        # Create tenant-scoped User
        user = User(
            identity_id=identity.id,
            display_name=user_info.name or username,
            avatar_url=user_info.avatar_url,
            registration_source=self.provider_type,
            tenant_id=tenant_id,
            is_active=True,
        )

        db.add(user)
        await db.flush()

        # Preload identity
        user.identity = identity
        return user


class MicrosoftTeamsAuthProvider(BaseAuthProvider):
    """Microsoft Teams OAuth provider implementation."""

    provider_type = "microsoft_teams"

    # Will be implemented when needed
    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        raise NotImplementedError("Microsoft Teams OAuth not yet implemented")

    async def exchange_code_for_token(self, code: str) -> dict:
        raise NotImplementedError("Microsoft Teams OAuth not yet implemented")

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        raise NotImplementedError("Microsoft Teams OAuth not yet implemented")


# Provider class mapping
PROVIDER_CLASSES = {
    "feishu": FeishuAuthProvider,
    "dingtalk": DingTalkAuthProvider,
    "wecom": WeComAuthProvider,
    "oauth2": OAuth2AuthProvider,
    "microsoft_teams": MicrosoftTeamsAuthProvider,
}
