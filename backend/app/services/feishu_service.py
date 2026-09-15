"""Feishu (Lark) provider API transport."""

import json
from collections import OrderedDict
from typing import TYPE_CHECKING

import httpx
from loguru import logger

try:
    import lark_oapi as lark
    _HAS_LARK = True
except ImportError:
    lark = None  # type: ignore
    _HAS_LARK = False
if TYPE_CHECKING:
    from lark_oapi import Client as LarkClient

FEISHU_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
FEISHU_SEND_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

class FeishuAPIError(RuntimeError):
    """Structured Feishu API error that preserves provider-returned details."""

    def __init__(
        self,
        *,
        stage: str,
        http_status: int | None = None,
        code: int | None = None,
        msg: str = "",
        log_id: str | None = None,
        troubleshooter: str | None = None,
        message_id: str | None = None,
    ):
        self.stage = stage
        self.http_status = http_status
        self.code = code
        self.msg = msg or "Unknown Feishu error"
        self.log_id = log_id
        self.troubleshooter = troubleshooter
        self.message_id = message_id

        parts = [f"Feishu {stage} failed"]
        if self.http_status is not None:
            parts.append(f"HTTP {self.http_status}")
        if self.code is not None:
            parts.append(f"code={self.code}")
        parts.append(f"msg={self.msg}")
        if self.log_id:
            parts.append(f"log_id={self.log_id}")
        if self.troubleshooter:
            parts.append(f"troubleshooter={self.troubleshooter}")
        super().__init__(", ".join(parts))

    @property
    def user_message(self) -> str:
        base = self.msg
        if self.code is not None:
            base = f"{base} (code {self.code})"
        if self.troubleshooter:
            return (
                f"{base}\n"
                f"{self.troubleshooter}"
            )
        return base


class FeishuService:
    """Bounded transport for Feishu provider APIs."""

    # Maximum number of lark SDK client instances to keep alive simultaneously.
    # Each entry corresponds to a unique (app_id, app_secret) pair.  Excess entries
    # are evicted in LRU order (oldest-accessed first) to bound memory usage in
    # long-running multi-tenant deployments.
    _LARK_CLIENT_CACHE_MAX = 50

    def __init__(self):
        # OrderedDict is used as a simple LRU cache: move_to_end() on each hit
        # keeps the most-recently-used entries at the tail so we can evict from
        # the head when the cache is full.
        self._lark_clients: OrderedDict[tuple[str, str], LarkClient] = OrderedDict()

    @staticmethod
    def _parse_api_response(
        resp: httpx.Response,
        *,
        stage: str,
        message_id: str | None = None,
    ) -> dict:
        """Parse Feishu API response and verify both HTTP status and business code."""
        try:
            data = resp.json()
        except Exception as e:
            logger.warning(
                f"[Feishu] {stage} returned non-JSON response "
                f"(http_status={resp.status_code}, message_id={message_id}): {e}"
            )
            raise FeishuAPIError(
                stage=stage,
                http_status=resp.status_code,
                msg="Provider returned invalid JSON",
                message_id=message_id,
            ) from e

        error_info = data.get("error") if isinstance(data, dict) else {}
        log_id = error_info.get("log_id") if isinstance(error_info, dict) else None
        troubleshooter = error_info.get("troubleshooter") if isinstance(error_info, dict) else None

        code = data.get("code") if isinstance(data, dict) else None
        msg = data.get("msg", "") if isinstance(data, dict) else ""

        if not 200 <= resp.status_code < 300:
            logger.warning(
                f"[Feishu] {stage} HTTP failure "
                f"(http_status={resp.status_code}, message_id={message_id}, body={str(data)[:300]})"
            )
            raise FeishuAPIError(
                stage=stage,
                http_status=resp.status_code,
                code=code,
                msg=msg or "Provider rejected the HTTP request",
                log_id=log_id,
                troubleshooter=troubleshooter,
                message_id=message_id,
            )

        if code != 0:
            logger.warning(
                f"[Feishu] {stage} business failure "
                f"(message_id={message_id}, code={code}, msg={msg})"
            )
            raise FeishuAPIError(
                stage=stage,
                http_status=resp.status_code,
                code=code,
                msg=msg or "Provider response omitted a successful business code",
                log_id=log_id,
                troubleshooter=troubleshooter,
                message_id=message_id,
            )

        return data

    async def get_tenant_access_token(
        self,
        app_id: str,
        app_secret: str,
    ) -> str:
        """Get a tenant access token for explicit application credentials."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(FEISHU_TENANT_TOKEN_URL, json={
                "app_id": app_id,
                "app_secret": app_secret,
            })
        data = self._parse_api_response(resp, stage="get_tenant_access_token")
        token = data.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuAPIError(
                stage="get_tenant_access_token",
                http_status=resp.status_code,
                code=data.get("code"),
                msg="Provider response omitted tenant_access_token",
            )
        return token

    async def list_bot_chats(
        self,
        app_id: str,
        app_secret: str,
        *,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict:
        """List groups joined by the configured bot using app identity."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        params: dict[str, str | int] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                FEISHU_CHAT_LIST_URL,
                headers={"Authorization": f"Bearer {tenant_token}"},
                params=params,
            )
        return self._parse_api_response(response, stage="list_bot_chats")

    async def send_message(
        self,
        app_id: str,
        app_secret: str,
        receive_id: str,
        msg_type: str,
        content: str,
        receive_id_type: str = "open_id",
        stage: str = "send_message",
    ) -> dict:
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
            data = self._parse_api_response(resp, stage=stage)
            return data

    async def patch_message(
        self,
        app_id: str,
        app_secret: str,
        message_id: str,
        content: str,
        stage: str = "patch_message",
    ) -> dict:
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
            data = self._parse_api_response(resp, stage=stage, message_id=message_id)
            return data

    async def add_message_reaction(
        self,
        app_id: str,
        app_secret: str,
        message_id: str,
        emoji_type: str,
        stage: str = "add_message_reaction",
    ) -> dict:
        """Add one bot-identity reaction to an existing Feishu message."""
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                FEISHU_APP_TOKEN_URL,
                json={"app_id": app_id, "app_secret": app_secret},
            )
            app_token = token_resp.json().get("app_access_token", "")
            resp = await client.post(
                f"{FEISHU_SEND_MSG_URL}/{message_id}/reactions",
                json={"reaction_type": {"emoji_type": emoji_type}},
                headers={"Authorization": f"Bearer {app_token}"},
            )
        return self._parse_api_response(resp, stage=stage, message_id=message_id)

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
            with open(fp, "rb") as f:  # noqa: ASYNC230 -- bytes must be materialized before multipart upload
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
                text_resp = await client.post(
                    f"{FEISHU_SEND_MSG_URL}?receive_id_type={receive_id_type}",
                    json={"receive_id": receive_id, "msg_type": "text",
                          "content": _json.dumps({"text": accompany_msg})},
                    headers=headers,
                )
                if text_resp.status_code != 200:
                    logger.error(
                        f"[Feishu] Failed to send text accompany message: "
                        f"status={text_resp.status_code}, body={text_resp.text}, "
                        f"receive_id={receive_id}, receive_id_type={receive_id_type}"
                    )

            # Send file message
            resp = await client.post(
                f"{FEISHU_SEND_MSG_URL}?receive_id_type={receive_id_type}",
                json={"receive_id": receive_id, "msg_type": "file",
                      "content": _json.dumps({"file_key": file_key})},
                headers=headers,
            )
            if resp.status_code != 200:
                logger.error(
                    f"[Feishu] Failed to send file message: "
                    f"status={resp.status_code}, body={resp.text}, "
                    f"receive_id={receive_id}, receive_id_type={receive_id_type}, "
                    f"file_key={file_key}"
                )
            return resp.json()

    # --- Bitable (多维表格) API ---

    async def bitable_list_tables(self, app_id: str, app_secret: str, app_token: str) -> dict:
        """List all tables in a Bitable app."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_list_tables",
            )

    async def bitable_list_fields(self, app_id: str, app_secret: str, app_token: str, table_id: str) -> dict:
        """List all fields in a specific table."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_list_fields",
            )

    async def bitable_query_records(
        self,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        filters: dict | None = None,
        *,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict:
        """Query records in a specific table."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        body = dict(filters) if filters else {}
        params: dict[str, str | int] = {
            "page_size": max(1, min(page_size, 500)),
        }
        if page_token:
            params["page_token"] = page_token
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
                json=body,
                params=params,
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_query_records",
            )

    async def bitable_create_record(self, app_id: str, app_secret: str, app_token: str, table_id: str, fields: dict) -> dict:
        """Create a new record in a specific table."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                json={"fields": fields},
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_create_record",
            )

    async def bitable_update_record(self, app_id: str, app_secret: str, app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
        """Update an existing record in a specific table."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                json={"fields": fields},
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_update_record",
            )
            
    async def bitable_delete_record(self, app_id: str, app_secret: str, app_token: str, table_id: str, record_id: str) -> dict:
        """Delete an existing record in a specific table."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(
                resp,
                stage="bitable_delete_record",
            )

    async def bitable_create_app(self, app_id: str, app_secret: str, name: str, folder_token: str = "") -> dict:
        """Create a new Bitable (多维表格) app.

        Uses the Bitable v1 apps API: POST /open-apis/bitable/v1/apps
        If folder_token is empty, the file is created in the root 'My Drive'.

        Args:
            name:         The display name of the new Bitable (max 255 chars).
            folder_token: Parent folder token (optional). Leave empty for root.
        Returns:
            API response dict containing 'data.app.app_token' as the new app_token.
        """
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        body: dict = {"name": name}
        if folder_token:
            body["folder_token"] = folder_token
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/bitable/v1/apps",
                json=body,
                headers={"Authorization": f"Bearer {tenant_token}"},
            )
            return self._parse_api_response(
                resp,
                stage="bitable_create_app",
            )


    # --- Docs API ---
    async def read_feishu_doc(self, app_id: str, app_secret: str, document_id: str) -> dict:
        """Get pure text content of a new-version Feishu Doc (docx)."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/raw_content",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(resp, stage="doc_read")

    async def create_feishu_doc(self, app_id: str, app_secret: str, folder_token: str | None = None, title: str = "Untitled Document") -> dict:
        """Create a new Feishu Doc (docx)."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        body = {"title": title}
        if folder_token:
            body["folder_token"] = folder_token
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/docx/v1/documents",
                json=body,
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return self._parse_api_response(resp, stage="doc_create")

    async def append_feishu_doc(self, app_id: str, app_secret: str, document_id: str, content: str) -> dict:
        """Append text to the end of a Feishu Doc (document_id is also the root block_id)."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        # Convert plain text to a text block
        body = {
            "children": [
                {
                    "block_type": 2, # Text block (paragraph)
                    "text": {
                        "elements": [
                            {
                                "text_run": {
                                    "content": content
                                }
                            }
                        ]
                    }
                }
            ]
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                json=body,
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return resp.json()

    async def append_feishu_doc_blocks(self, app_id: str, app_secret: str, document_id: str, block_id: str, blocks: list) -> dict:
        """Append pre-parsed Markdown blocks to a Feishu doc block (e.g., body_block_id)."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/children",
                json={"children": blocks},
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return resp.json()

    # --- Approval API ---
    async def create_approval_instance(self, app_id: str, app_secret: str, approval_code: str, user_id: str, form_data: str) -> dict:
        """Create a Feishu approval instance."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        body = {
            "approval_code": approval_code,
            "user_id": user_id,
            "form": form_data
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/approval/v4/instances",
                json=body,
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return resp.json()

    async def query_approval_instances(
        self,
        app_id: str,
        app_secret: str,
        approval_code: str,
        status: str | None = None,
    ) -> dict:
        """Query Feishu approval instances."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        body = {"approval_code": approval_code}
        if status:
            body["status"] = status
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/approval/v4/instances/query",
                json=body,
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return resp.json()

    async def get_approval_instance(self, app_id: str, app_secret: str, instance_id: str) -> dict:
        """Get details of a specific Feishu approval instance."""
        tenant_token = await self.get_tenant_access_token(app_id, app_secret)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://open.feishu.cn/open-apis/approval/v4/instances/{instance_id}",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            return resp.json()

    # --- CardKit Streaming API ---

    def _get_lark_client(self, app_id: str, app_secret: str):
        """Get or create a cached lark-oapi SDK client for the given app credentials.

        Implements a simple LRU eviction policy: when the cache exceeds
        _LARK_CLIENT_CACHE_MAX entries, the least-recently-used client is removed.
        """
        if not _HAS_LARK or lark is None:
            raise RuntimeError("lark-oapi package is not installed. Install with: pip install lark-oapi")
        cache_key = (app_id, app_secret)
        client = self._lark_clients.get(cache_key)
        if client is None:
            # Evict the oldest entry if the cache is at capacity.
            if len(self._lark_clients) >= self._LARK_CLIENT_CACHE_MAX:
                (evicted_app_id, _), _ = self._lark_clients.popitem(last=False)
                logger.debug(f"[Feishu] _lark_clients LRU evict: app_id={evicted_app_id}")
            client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
            self._lark_clients[cache_key] = client
        else:
            # Move hit entry to the tail so it is considered most-recently-used.
            self._lark_clients.move_to_end(cache_key)
        return client

    async def create_card_entity(
        self,
        app_id: str,
        app_secret: str,
        card_dict: dict,
    ) -> str:
        """Create a CardKit card entity and return its card_id."""
        from lark_oapi.api.cardkit.v1.model import (
            CreateCardRequest,
            CreateCardRequestBody,
        )

        client = self._get_lark_client(app_id, app_secret)
        body = CreateCardRequestBody.builder() \
            .type("card_json") \
            .data(json.dumps(card_dict)) \
            .build()
        request = CreateCardRequest.builder().request_body(body).build()
        cardkit = client.cardkit
        if cardkit is None:
            raise RuntimeError("Feishu CardKit client is unavailable")

        try:
            resp = await cardkit.v1.card.acreate(request)
            logger.info(
                f"[Feishu CardKit] create_card_entity response: "
                f"code={resp.code}, msg={resp.msg}"
            )
            if not resp.success():
                raise RuntimeError(
                    f"Feishu CardKit create_card_entity failed: code={resp.code}, msg={resp.msg}"
                )
            if resp.data is None or not resp.data.card_id:
                raise RuntimeError(
                    "Feishu CardKit create_card_entity returned no card_id"
                )
            return resp.data.card_id
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"[Feishu CardKit] create_card_entity error: {e}")
            raise RuntimeError(f"Feishu CardKit create_card_entity error: {e}") from e

    async def send_card_by_card_id(
        self,
        app_id: str,
        app_secret: str,
        receive_id: str,
        card_id: str,
        receive_id_type: str = "open_id",
    ) -> None:
        """Send an interactive message referencing an existing card_id."""
        content = json.dumps({
            "type": "card",
            "data": {"card_id": card_id},
        })
        await self.send_message(
            app_id=app_id,
            app_secret=app_secret,
            receive_id=receive_id,
            msg_type="interactive",
            content=content,
            receive_id_type=receive_id_type,
            stage="send_card_by_card_id",
        )

    async def stream_card_content(
        self,
        app_id: str,
        app_secret: str,
        card_id: str,
        element_id: str,
        content: str,
        sequence: int,
    ) -> None:
        """Stream content to a specific card element via CardKit API."""
        from lark_oapi.api.cardkit.v1.model import (
            ContentCardElementRequest,
            ContentCardElementRequestBody,
        )

        client = self._get_lark_client(app_id, app_secret)
        body = ContentCardElementRequestBody.builder() \
            .content(content) \
            .sequence(sequence) \
            .build()
        request = ContentCardElementRequest.builder() \
            .card_id(card_id) \
            .element_id(element_id) \
            .request_body(body) \
            .build()
        cardkit = client.cardkit
        if cardkit is None:
            raise RuntimeError("Feishu CardKit client is unavailable")

        try:
            resp = await cardkit.v1.card_element.acontent(request)
            logger.info(
                f"[Feishu CardKit] stream_card_content response: "
                f"code={resp.code}, msg={resp.msg}, card_id={card_id}, "
                f"element_id={element_id}, sequence={sequence}"
            )
            if not resp.success():
                raise RuntimeError(
                    f"Feishu CardKit stream_card_content failed: "
                    f"code={resp.code}, msg={resp.msg}"
                )
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"[Feishu CardKit] stream_card_content error: {e}")
            raise RuntimeError(f"Feishu CardKit stream_card_content error: {e}") from e

    async def set_card_streaming_mode(
        self,
        app_id: str,
        app_secret: str,
        card_id: str,
        streaming_mode: int,
        sequence: int,
    ) -> None:
        """Toggle streaming mode on a card via CardKit settings API."""
        from lark_oapi.api.cardkit.v1.model import (
            SettingsCardRequest,
            SettingsCardRequestBody,
        )

        client = self._get_lark_client(app_id, app_secret)
        body = SettingsCardRequestBody.builder() \
            .settings(json.dumps({"streaming_mode": streaming_mode})) \
            .sequence(sequence) \
            .build()
        request = SettingsCardRequest.builder() \
            .card_id(card_id) \
            .request_body(body) \
            .build()
        cardkit = client.cardkit
        if cardkit is None:
            raise RuntimeError("Feishu CardKit client is unavailable")

        try:
            resp = await cardkit.v1.card.asettings(request)
            logger.info(
                f"[Feishu CardKit] set_card_streaming_mode response: "
                f"code={resp.code}, msg={resp.msg}, card_id={card_id}, "
                f"streaming_mode={streaming_mode}, sequence={sequence}"
            )
            if not resp.success():
                raise RuntimeError(
                    f"Feishu CardKit set_card_streaming_mode failed: "
                    f"code={resp.code}, msg={resp.msg}"
                )
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"[Feishu CardKit] set_card_streaming_mode error: {e}")
            raise RuntimeError(f"Feishu CardKit set_card_streaming_mode error: {e}") from e

    async def update_cardkit_card(
        self,
        app_id: str,
        app_secret: str,
        card_id: str,
        card_dict: dict,
        sequence: int,
    ) -> None:
        """Full card update via CardKit API."""
        from lark_oapi.api.cardkit.v1.model import (
            Card,
            UpdateCardRequest,
            UpdateCardRequestBody,
        )

        client = self._get_lark_client(app_id, app_secret)
        card = Card.builder() \
            .type("card_json") \
            .data(json.dumps(card_dict)) \
            .build()
        body = UpdateCardRequestBody.builder() \
            .card(card) \
            .sequence(sequence) \
            .build()
        request = UpdateCardRequest.builder() \
            .card_id(card_id) \
            .request_body(body) \
            .build()
        cardkit = client.cardkit
        if cardkit is None:
            raise RuntimeError("Feishu CardKit client is unavailable")

        try:
            resp = await cardkit.v1.card.aupdate(request)
            logger.info(
                f"[Feishu CardKit] update_cardkit_card response: "
                f"code={resp.code}, msg={resp.msg}, card_id={card_id}, "
                f"sequence={sequence}"
            )
            if not resp.success():
                raise RuntimeError(
                    f"Feishu CardKit update_cardkit_card failed: "
                    f"code={resp.code}, msg={resp.msg}"
                )
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"[Feishu CardKit] update_cardkit_card error: {e}")
            raise RuntimeError(f"Feishu CardKit update_cardkit_card error: {e}") from e


feishu_service = FeishuService()
