"""Feishu (Lark) OAuth and API integration service."""

import httpx
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.user import User
from app.models.identity import IdentityProvider

settings = get_settings()

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
FEISHU_SEND_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuService:
    """Service for Feishu OAuth login and message API."""

    def __init__(self):
        self.app_id = settings.FEISHU_APP_ID
        self.app_secret = settings.FEISHU_APP_SECRET
        self._app_access_token: str | None = None

    async def get_app_access_token(self) -> str:
        """Get or refresh the app-level access token."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            })
            data = resp.json()
            self._app_access_token = data.get("app_access_token", "")
            return self._app_access_token

    async def exchange_code_for_user(self, code: str) -> dict:
        """Exchange OAuth authorization code for user info.

        Returns dict with: open_id, union_id, user_id, name, email, avatar_url
        """
        app_token = await self.get_app_access_token()

        async with httpx.AsyncClient() as client:
            # Get user access token
            token_resp = await client.post(FEISHU_TOKEN_URL, json={
                "grant_type": "authorization_code",
                "code": code,
            }, headers={"Authorization": f"Bearer {app_token}"})
            token_data = token_resp.json()
            user_access_token = token_data.get("data", {}).get("access_token", "")

            # Get user info
            info_resp = await client.get(FEISHU_USER_INFO_URL, headers={
                "Authorization": f"Bearer {user_access_token}",
            })
            info_data = info_resp.json().get("data", {})

            return {
                "open_id": info_data.get("open_id"),
                "union_id": info_data.get("union_id"),
                "user_id": info_data.get("user_id"),
                "name": info_data.get("name", ""),
                "email": info_data.get("email", ""),
                "avatar_url": info_data.get("avatar_url", ""),
            }

    async def login_or_register(self, db: AsyncSession, feishu_user: dict, tenant_id: str | None = None) -> tuple[User, str]:
        """Login existing user or register new one via Feishu SSO.

        Uses OrgMember as the identity anchor (synced from Feishu org directory).
        Returns (user, jwt_token)
        """
        from app.models.org import OrgMember

        open_id = feishu_user["open_id"]
        user_id = feishu_user.get("user_id", "")
        union_id = feishu_user.get("union_id")
        fs_email = feishu_user.get("email", "")
        fs_name = feishu_user.get("name", "")
        fs_avatar = feishu_user.get("avatar_url", "")

        # Resolve provider (needed for OrgMember.provider_id scoping)
        provider_query = select(IdentityProvider).where(IdentityProvider.provider_type == "feishu")
        provider_query = provider_query.where(IdentityProvider.tenant_id == tenant_id)
        provider_result = await db.execute(provider_query)
        provider = provider_result.scalar_one_or_none()
        if not provider:
            provider = IdentityProvider(
                provider_type="feishu",
                name="Feishu",
                is_active=True,
                config={"app_id": self.app_id, "app_secret": self.app_secret},
                tenant_id=tenant_id,
            )
            db.add(provider)
            await db.flush()

        # 1. Look up OrgMember by open_id (primary) or external_id (user_id)
        #    Also filter by tenant_id and provider_id for accuracy
        member = None
        if open_id:
            member_r = await db.execute(
                select(OrgMember).where(
                    OrgMember.open_id == open_id,
                    OrgMember.provider_id == provider.id,
                    OrgMember.status == "active",
                )
            )
            member = member_r.scalar_one_or_none()
        if not member and user_id:
            member_r = await db.execute(
                select(OrgMember).where(
                    OrgMember.external_id == user_id,
                    OrgMember.provider_id == provider.id,
                    OrgMember.status == "active",
                )
            )
            member = member_r.scalar_one_or_none()

        # 2. Resolve User from OrgMember
        user = None
        if member and member.user_id:
            u_result = await db.execute(select(User).where(User.id == member.user_id))
            user = u_result.scalar_one_or_none()

        # 3. Fallback: find by email or primary_mobile (new generic fields)
        if not user and fs_email:
            query = select(User).where(User.email == fs_email)
            if tenant_id:
                query = query.where(User.tenant_id == tenant_id)
            result = await db.execute(query)
            user = result.scalar_one_or_none()

        if user:
            # Existing user — sync latest profile from Feishu
            if fs_avatar:
                user.avatar_url = fs_avatar
            if (not user.email or user.email.endswith("@feishu.local")) and fs_email:
                user.email = fs_email
            if fs_name:
                user.display_name = fs_name
            # Update identity fields (user_id only)
            if user_id:
                user.external_id = user_id
                user.feishu_user_id = user_id
            # Link to OrgMember if not yet bound
            if member and not member.user_id:
                member.user_id = user.id
        else:
            # New user — create account
            username = fs_email.split("@")[0] if fs_email else f"feishu_{open_id[:8]}"
            email = fs_email or f"{username}@feishu.local"

            # Ensure unique username
            existing_r = await db.execute(select(User).where(User.username == username))
            if existing_r.scalar_one_or_none():
                username = f"{username}_{open_id[:6]}"

            user = User(
                username=username,
                email=email,
                password_hash=hash_password(open_id),
                display_name=fs_name or username,
                avatar_url=fs_avatar or None,
                external_id=user_id,
                feishu_user_id=user_id,
                registration_source="feishu",
                tenant_id=tenant_id,
            )
            db.add(user)
            await db.flush()

            # Link back to OrgMember if found
            if member:
                member.user_id = user.id

        await db.flush()

        token = create_access_token(str(user.id), user.role)
        return user, token


    async def send_message(self, app_id: str, app_secret: str, receive_id: str,
                           msg_type: str, content: str, receive_id_type: str = "open_id") -> dict:
        """Send a message via a specific Feishu bot (per-agent credentials).

        Args:
            app_id: The Feishu app's App ID (per-agent)
            app_secret: The Feishu app's App Secret (per-agent)
            receive_id: Target user's open_id
            msg_type: "text", "interactive", etc.
            content: JSON string of message content
            receive_id_type: "open_id" or "chat_id"
        """
        # Get app access token for this specific agent's bot
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")

            resp = await client.post(
                f"{FEISHU_SEND_MSG_URL}?receive_id_type={receive_id_type}",
                json={
                    "receive_id": receive_id,
                    "msg_type": msg_type,
                    "content": content,
                },
                headers={"Authorization": f"Bearer {app_token}"},
            )
            return resp.json()

    async def patch_message(self, app_id: str, app_secret: str, message_id: str, content: str) -> dict:
        """Patch an existing message (e.g. updating an interactive card for streaming)."""
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")

            resp = await client.patch(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                json={
                    "content": content,
                },
                headers={"Authorization": f"Bearer {app_token}"},
            )
            return resp.json()

    async def resolve_open_id(self, app_id: str, app_secret: str,
                               email: str | None = None, mobile: str | None = None) -> str | None:
        """Resolve a user's open_id for a specific app using email or mobile.

        Each Feishu app gets a unique open_id per user. This method looks up the
        correct open_id for the given app's credentials.
        """
        if not email and not mobile:
            return None

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")

            body: dict = {}
            if email:
                body["emails"] = [email]
            if mobile:
                body["mobiles"] = [mobile]

            resp = await client.post(
                "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id",
                json=body,
                headers={"Authorization": f"Bearer {app_token}"},
                params={"user_id_type": "open_id"},
            )
            data = resp.json()
            if data.get("code") != 0:
                return None

            user_list = data.get("data", {}).get("user_list", [])
            for u in user_list:
                oid = u.get("user_id")
                if oid:
                    return oid
            return None

    async def resolve_user_id(self, app_id: str, app_secret: str,
                               email: str | None = None, mobile: str | None = None) -> str | None:
        """Resolve a user's tenant-level user_id using email or mobile.

        Unlike open_id, user_id is stable across all apps within the same tenant.
        Requires contact:user.employee_id:readonly permission.
        """
        if not email and not mobile:
            return None

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")

            body: dict = {}
            if email:
                body["emails"] = [email]
            if mobile:
                body["mobiles"] = [mobile]

            resp = await client.post(
                "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id",
                json=body,
                headers={"Authorization": f"Bearer {app_token}"},
                params={"user_id_type": "user_id"},
            )
            data = resp.json()
            if data.get("code") != 0:
                return None

            user_list = data.get("data", {}).get("user_list", [])
            for u in user_list:
                uid = u.get("user_id")
                if uid:
                    return uid
            return None

    async def send_approval_card(self, app_id: str, app_secret: str,
                                  creator_open_id: str, agent_name: str,
                                  action_type: str, details: str, approval_id: str) -> dict:
        """Send an interactive approval card to the agent creator via Feishu."""
        import json
        card_content = json.dumps({
            "type": "template",
            "data": {
                "template_id": "",  # Use custom card
                "template_variable": {
                    "agent_name": agent_name,
                    "action_type": action_type,
                    "details": details,
                    "approval_id": approval_id,
                }
            }
        })
        # Simplified — in production, use Feishu interactive card JSON
        text_content = json.dumps({
            "text": f"🔴 [{agent_name}] 请求审批\n操作: {action_type}\n详情: {details}\n\n请在 Clawith 平台审批。"
        })
        return await self.send_message(app_id, app_secret, creator_open_id, "text", text_content)

    async def download_message_resource(self, app_id: str, app_secret: str,
                                         message_id: str, file_key: str,
                                         resource_type: str = "file") -> bytes:
        """Download a file or image from a Feishu message.

        Args:
            resource_type: "file" or "image"
        Returns raw file bytes.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")
            resp = await client.get(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}",
                params={"type": resource_type},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()
            return resp.content

    async def upload_and_send_file(self, app_id: str, app_secret: str,
                                    receive_id: str, file_path,
                                    receive_id_type: str = "open_id",
                                    accompany_msg: str = "") -> dict:
        """Upload a local file to Feishu and send it as a file message.

        Returns the send_message response dict.
        """
        import json as _json
        from pathlib import Path as _Path
        fp = _Path(file_path)
        async with httpx.AsyncClient(timeout=60) as client:
            # Get token
            token_resp = await client.post(FEISHU_APP_TOKEN_URL, json={
                "app_id": app_id, "app_secret": app_secret,
            })
            app_token = token_resp.json().get("app_access_token", "")
            headers = {"Authorization": f"Bearer {app_token}"}

            # Upload file
            with open(fp, "rb") as f:
                file_bytes = f.read()
            # Determine file type for Feishu upload
            ext = fp.suffix.lower()
            feishu_file_type = "stream"  # generic binary
            if ext in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md"):
                feishu_file_type = "stream"
            upload_resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                files={"file": (fp.name, file_bytes, "application/octet-stream")},
                data={"file_type": feishu_file_type, "file_name": fp.name},
                headers=headers,
            )
            upload_data = upload_resp.json()
            if upload_data.get("code") != 0:
                raise RuntimeError(f"Feishu file upload failed: {upload_data.get('msg')}")
            file_key = upload_data["data"]["file_key"]

            # Send text accompany message first if provided
            if accompany_msg:
                await client.post(
                    f"{FEISHU_SEND_MSG_URL}?receive_id_type={receive_id_type}",
                    json={"receive_id": receive_id, "msg_type": "text",
                          "content": _json.dumps({"text": accompany_msg})},
                    headers=headers,
                )

            # Send file message
            resp = await client.post(
                f"{FEISHU_SEND_MSG_URL}?receive_id_type={receive_id_type}",
                json={"receive_id": receive_id, "msg_type": "file",
                      "content": _json.dumps({"file_key": file_key})},
                headers=headers,
            )
            return resp.json()


feishu_service = FeishuService()
