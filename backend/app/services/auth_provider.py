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
            db,
            provider_user_id,
            self.provider_type,
            tenant_id=tenant_id,
            identity_data=user_info.raw_data,
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

        if user:
            # Update user info and ensure identity is loaded
            if not user.identity_id:
                 from app.services.registration_service import registration_service
                 identity = await registration_service.find_or_create_identity(db, email=user_info.email, phone=user_info.mobile)
                 user.identity_id = identity.id
            
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
        
        # Preload identity
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
        # Contact.User.Read is required for GET /v1.0/contact/users/me (user info on callback)
        # contact.user.mobile requires the fieldMobile permission in DingTalk console
        # fieldEmail requires the fieldEmail permission in DingTalk console
        scope = "openid corpid Contact.User.Read fieldEmail contact.user.mobile"
        params = (
            f"client_id={app_id}&redirect_uri={quote(redirect_uri)}&"
            f"state={state}&response_type=code&scope={quote(scope)}&prompt=consent"
        )
        # corp_id is optional: restricts the login page to a specific enterprise.
        # If not configured, DingTalk shows a company picker (still works for SSO).
        if self.corp_id:
            params = f"corpId={self.corp_id}&" + params
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
                # Common error: errCode=403 means Contact.User.Read scope not granted.
                # Ensure 'Contact.User.Read' is included in the OAuth scope AND
                # that the app has been authorized by the employee in the login flow.
                err_msg = info_data.get('message') or info_data.get('errmsg') or str(info_data)
                logger.error(
                    f"DingTalk user info fetch failed (HTTP {info_resp.status_code}): {info_data}. "
                    "This usually means the 'Contact.User.Read' OAuth scope is missing from "
                    "the authorization URL, or the app lacks the corresponding permission."
                )
                raise Exception(f"Failed to fetch user info: {err_msg}")

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
    """WeCom (Enterprise WeChat) OAuth provider implementation.

    Authentication flow:
    1. gettoken (corp_id + secret) -> access_token
    2. auth/getuserinfo (access_token + OAuth code) -> userid + user_ticket
    3. auth/getuserdetail (access_token + user_ticket) -> avatar, email, mobile
    4. user/get (access_token + userid) -> name, position (non-sensitive fields)

    Note: Steps 3 and 4 require the calling server IP to be whitelisted in the
    WeCom self-built app settings. This is a one-time setup per tenant.
    (Contrast with getuserinfo in step 2, which only requires trusted domain,
    not IP whitelist.)
    """

    provider_type = "wecom"

    # All WeCom self-built app API calls go to qyapi.weixin.qq.com
    # The old api.weixin.qq.com endpoints are legacy WeCom Public Account APIs
    # and no longer work for self-built apps.
    WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    WECOM_USER_INFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"
    WECOM_USER_DETAIL_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserdetail"
    WECOM_USER_GET_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/get"

    def __init__(self, provider: IdentityProvider | None = None, config: dict | None = None):
        super().__init__(provider, config)
        # corp_id and agent_id are used for the OAuth redirect URL
        self.corp_id = self.config.get("corp_id") or self.config.get("app_id")
        # secret is the self-built app's AgentSecret (not the contact-sync secret)
        self.secret = self.config.get("secret") or self.config.get("app_secret")
        self.agent_id = self.config.get("agent_id")

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Construct the WeCom web-login SSO redirect URL.

        Uses the 'Scan QR Code to Login' flow (CorpPinCorp), which redirects users
        to authenticate with their WeCom account then returns them to redirect_uri
        with a code parameter.
        """
        from urllib.parse import quote
        base_url = "https://open.work.weixin.qq.com/wwlogin/sso/login"
        params = (
            f"loginType=CorpPinCorp"
            f"&appid={self.corp_id}"
            f"&agentid={self.agent_id}"
            f"&redirect_uri={quote(redirect_uri)}"
            f"&state={state}"
        )
        return f"{base_url}?{params}"

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange OAuth code for a packed token string containing all user data.

        Three sequential API calls:
          1. gettoken -> access_token
          2. auth/getuserinfo (code) -> userid + user_ticket
          3a. auth/getuserdetail (user_ticket) -> avatar, email, mobile [sensitive]
          3b. user/get (userid) -> name, position [non-sensitive, best-effort]

        Returns a packed JSON dict disguised as the access_token field so
        the existing BaseAuthProvider interface (get_user_info) can consume it.
        """
        import json

        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: Get app-level access token using corp credentials
            token_resp = await client.get(
                self.WECOM_TOKEN_URL,
                params={"corpid": self.corp_id, "corpsecret": self.secret},
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                logger.error(f"[WeCom SSO] gettoken failed: {token_data}")
                return {}

            # Step 2: Exchange OAuth code for userid + user_ticket
            # auth/getuserinfo returns userid (lowercase 'u') for internal employees.
            # user_ticket is a temporary credential (valid ~1800s) representing
            # the employee's own OAuth authorization, required for sensitive fields.
            info_resp = await client.get(
                self.WECOM_USER_INFO_URL,
                params={"access_token": access_token, "code": code},
            )
            info_data = info_resp.json()
            # The key is lowercase 'userid' in the new auth endpoint (not 'UserId')
            userid = info_data.get("userid") or info_data.get("UserId", "")
            user_ticket = info_data.get("user_ticket", "")
            if not userid:
                logger.error(f"[WeCom SSO] getuserinfo missing userid: {info_data}")
                return {}

            # Step 3a: Fetch sensitive profile fields using user_ticket.
            # Since June 2022, new self-built apps cannot get avatar/email/mobile
            # from user/get directly. The user_ticket (from OAuth consent) unlocks them.
            # Returns: userid, gender, avatar, qr_code, mobile, email, biz_mail, address
            sensitive_data: dict = {}
            if user_ticket:
                try:
                    detail_resp = await client.post(
                        self.WECOM_USER_DETAIL_URL,
                        params={"access_token": access_token},
                        json={"user_ticket": user_ticket},
                    )
                    detail_json = detail_resp.json()
                    if detail_json.get("errcode") == 0:
                        sensitive_data = detail_json
                        logger.info(f"[WeCom SSO] getuserdetail succeeded for {userid}")
                    else:
                        logger.warning(f"[WeCom SSO] getuserdetail failed: {detail_json}")
                except Exception as e:
                    logger.warning(f"[WeCom SSO] getuserdetail error: {e}")
            else:
                logger.info(
                    f"[WeCom SSO] No user_ticket for {userid}; "
                    "sensitive fields (avatar/email/mobile) will be unavailable. "
                    "Ensure the WeCom app has 'snsapi_privateinfo' scope."
                )

            # Step 3b: Fetch non-sensitive profile fields from user/get (name, position).
            # These fields are NOT restricted by the June 2022 policy and are available
            # via the standard app access token (IP whitelist required).
            basic_data: dict = {}
            try:
                get_resp = await client.get(
                    self.WECOM_USER_GET_URL,
                    params={"access_token": access_token, "userid": userid},
                )
                get_json = get_resp.json()
                if get_json.get("errcode") == 0:
                    basic_data = get_json
                    logger.info(f"[WeCom SSO] user/get succeeded for {userid}")
                else:
                    logger.warning(f"[WeCom SSO] user/get failed: {get_json}")
            except Exception as e:
                logger.warning(f"[WeCom SSO] user/get error: {e}")

            # Pack all data for get_user_info() to consume
            packed_token = json.dumps({
                "userid": userid,
                "sensitive": sensitive_data,  # from getuserdetail (avatar, email, mobile)
                "basic": basic_data,           # from user/get (name, position)
            })
            return {"access_token": packed_token}

    async def get_user_info(self, access_token: str) -> ExternalUserInfo:
        """Parse the packed token into a standardized ExternalUserInfo.

        Priority for each field:
          - email: sensitive_data (getuserdetail) > biz_mail > basic_data (user/get)
          - avatar: sensitive_data > basic_data
          - mobile: sensitive_data only (restricted post-2022 in user/get)
          - name: basic_data (non-sensitive, from user/get)
        """
        import json
        try:
            data = json.loads(access_token)
            userid = data.get("userid", "")
            sensitive = data.get("sensitive", {})
            basic = data.get("basic", {})

            # Name from user/get (non-sensitive, always available when IP is whitelisted)
            name = basic.get("name") or f"WeCom {userid}"

            # Email: prefer personal email from getuserdetail, fall back to biz_mail
            email = (
                sensitive.get("email")
                or sensitive.get("biz_mail")
                or basic.get("email")
                or basic.get("biz_mail")
                or ""
            )

            # Avatar from getuserdetail (restricted post-2022 in user/get)
            avatar_url = sensitive.get("avatar") or basic.get("avatar") or ""

            # Mobile only from getuserdetail (restricted post-2022 in user/get)
            mobile = sensitive.get("mobile") or ""

            # Merge raw_data so OrgMember has full context
            raw = {**basic, **sensitive, "userid": userid}

            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id=userid,
                name=name,
                email=email,
                avatar_url=avatar_url,
                mobile=mobile,
                raw_data=raw,
            )
        except Exception as e:
            logger.error(f"[WeCom SSO] get_user_info parse error: {e}")
            return ExternalUserInfo(
                provider_type=self.provider_type,
                provider_user_id="",
                name="",
                raw_data={"error": str(e)},
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
