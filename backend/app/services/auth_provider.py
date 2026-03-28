"""Generic OAuth/SSO authentication provider framework.

This module provides a base class for all identity providers (Feishu, DingTalk, WeCom, etc.)
and concrete implementations for each supported provider.
"""

import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.models.identity import IdentityProvider
from app.models.user import User
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
        """Find existing user or create new one via OrgMember.

        Args:
            db: Database session
            user_info: User info from provider
            tenant_id: Optional tenant ID for association

        Returns:
            Tuple of (user, is_new) where is_new indicates if user was created
        """
        from app.services.sso_service import sso_service

        # Ensure provider exists
        await self._ensure_provider(db, tenant_id)

        # 1. Try lookup via sso_service (which now uses OrgMember)
        # Prefer unionid if available, fallback to provider_user_id
        provider_user_id = user_info.provider_union_id or user_info.provider_user_id
        user = await sso_service.resolve_user_identity(
            db, provider_user_id, self.provider_type, tenant_id=tenant_id
        )
        # Feishu: if union_id lookup misses, fall back to open_id (org sync app may not return union_id)
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
            # 2. Fallback to legacy columns on User table
            user = await self._find_user_by_legacy_fields(db, user_info)

        # 3. Also try matching by email if available
        if not user and user_info.email:
            user = await sso_service.match_user_by_email(db, user_info.email, tenant_id)
            if user:
                # Link identity (OrgMember) to existing user
                await sso_service.link_identity(
                    db,
                    str(user.id),
                    self.provider_type,
                    provider_user_id,
                    user_info.raw_data,
                    tenant_id=tenant_id,
                )

        # 4. Also try matching by mobile if available (critical to prevent duplicate users)
        if not user and user_info.mobile:
            user = await sso_service.match_user_by_mobile(db, user_info.mobile, tenant_id)
            if user:
                # Link identity (OrgMember) to existing user
                await sso_service.link_identity(
                    db,
                    str(user.id),
                    self.provider_type,
                    provider_user_id,
                    user_info.raw_data,
                    tenant_id=tenant_id,
                )

        if user:
            # Update user info
            await self._update_existing_user(db, user, user_info)
        else:
            # Create new user
            user = await self._create_new_user(db, user_info, tenant_id)
            is_new = True
            
            # Link identity (OrgMember) to the new user
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
        if user_info.email and not user.email:
            user.email = user_info.email
        if user_info.mobile and not user.primary_mobile:
            user.primary_mobile = user_info.mobile

        # Update legacy fields if applicable
        await self._update_legacy_user_fields(user, user_info)

    async def _create_new_user(
        self, db: AsyncSession, user_info: ExternalUserInfo, tenant_id: str | None
    ) -> User:
        """Create new user from external identity."""
        username = user_info.email.split("@")[0] if user_info.email else f"{self.provider_type}_{user_info.provider_user_id[:8]}"

        # Ensure unique username
        existing = await db.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none():
            username = f"{username}_{user_info.provider_user_id[:6]}"

        email = user_info.email or f"{username}@{self.provider_type}.local"

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(user_info.provider_user_id),
            display_name=user_info.name or username,
            avatar_url=user_info.avatar_url,
            primary_mobile=user_info.mobile,
            registration_source=self.provider_type,
            tenant_id=tenant_id,
        )

        # Set legacy fields
        await self._set_legacy_user_fields(user, user_info)

        db.add(user)
        await db.flush()

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
        if self._app_access_token:
            return self._app_access_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.FEISHU_APP_TOKEN_URL,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            self._app_access_token = data.get("app_access_token", "")
            return self._app_access_token

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
            logger.info(f"WeCom user auth info: {user_data}")
            return user_data

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        # WeCom returns user info in the token exchange response
        logger.info("WeCom get_user_info called (user info usually handled in exchange_code)")
        return ExternalUserInfo(
            provider_type=self.provider_type,
            provider_user_id="",
            name="",
            raw_data={"wecom": "user_info_in_token_response"},
        )


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
    "microsoft_teams": MicrosoftTeamsAuthProvider,
}
