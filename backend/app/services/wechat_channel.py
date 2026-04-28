"""WeChat iLink Bot long-poll manager and client helpers."""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.agent import Agent as AgentModel
from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
from app.models.audit import ChatMessage
from app.models.channel_config import ChannelConfig
from app.services.channel_session import find_or_create_channel_session
from app.services.channel_user_service import channel_user_service


WECHAT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WECHAT_CHANNEL_VERSION = "1.0.0"
WECHAT_TEXT_LIMIT = 2000


class WeChatSessionExpiredError(RuntimeError):
    """Raised when the remote iLink session has expired."""


def random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN according to the protocol spec."""
    value = int.from_bytes(os.urandom(4), "big", signed=False)
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


def build_wechat_headers(token: str, route_tag: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": random_wechat_uin(),
    }
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


def split_wechat_text(text: str, limit: int = WECHAT_TEXT_LIMIT) -> list[str]:
    """Split text conservatively following the protocol's 2000-char guidance."""
    remaining = text or ""
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        segment = remaining[:limit]
        cut = max(segment.rfind("\n\n"), segment.rfind("\n"), segment.rfind(" "))
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks or [""]


async def send_wechat_text_message(
    *,
    token: str,
    base_url: str,
    to_user_id: str,
    context_token: str,
    text: str,
    route_tag: str | None = None,
) -> None:
    """Send one or more WeChat iLink text messages."""
    async with httpx.AsyncClient(timeout=20) as client:
        for chunk in split_wechat_text(text):
            resp = await client.post(
                f"{base_url.rstrip('/')}/ilink/bot/sendmessage",
                headers=build_wechat_headers(token, route_tag=route_tag),
                json={
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": to_user_id,
                        "client_id": f"clawith-wechat:{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
                        "message_type": 2,
                        "message_state": 2,
                        "context_token": context_token,
                        "item_list": [
                            {
                                "type": 1,
                                "text_item": {
                                    "text": chunk,
                                },
                            }
                        ],
                    },
                    "base_info": {
                        "channel_version": WECHAT_CHANNEL_VERSION,
                    },
                },
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"WeChat sendmessage failed: {resp.text[:300]}")


def _extract_wechat_text(item_list: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for item in item_list or []:
        if item.get("type") == 1:
            text = ((item.get("text_item") or {}).get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


async def _process_wechat_message(agent_id: uuid.UUID, msg: dict[str, Any], config: ChannelConfig) -> None:
    from app.api.feishu import _call_agent_llm
    from app.services.activity_logger import log_activity

    from_user_id = str(msg.get("from_user_id") or "").strip()
    if not from_user_id or from_user_id == (config.app_id or "").strip():
        return

    user_text = _extract_wechat_text(msg.get("item_list"))
    if not user_text:
        return

    context_token = str(msg.get("context_token") or "").strip()
    if not context_token:
        logger.warning(f"[WeChat] Missing context_token for agent {agent_id}, message skipped")
        return

    async with async_session() as db:
        agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            return

        extra_info = {
            "name": f"WeChat User {from_user_id[:8]}",
            "external_id": from_user_id,
        }
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="wechat",
            external_user_id=from_user_id,
            extra_info=extra_info,
        )
        platform_user_id = platform_user.id
        conv_key = str(msg.get("session_id") or from_user_id).strip()
        conv_id = f"wechat_{conv_key}"

        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=platform_user_id,
            external_conv_id=conv_id,
            source_channel="wechat",
            first_message_title=user_text,
        )
        session_conv_id = str(sess.id)

        history_r = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE)
        )
        history = [{"role": m.role, "content": m.content} for m in reversed(history_r.scalars().all())]

        db.add(
            ChatMessage(
                agent_id=agent_id,
                user_id=platform_user_id,
                role="user",
                content=user_text,
                conversation_id=session_conv_id,
            )
        )
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        reply_text = await _call_agent_llm(
            db=db,
            agent_id=agent_id,
            user_text=user_text,
            history=history,
            user_id=platform_user_id,
            session_id=session_conv_id,
        )

        token = str((config.extra_config or {}).get("bot_token") or "").strip()
        base_url = str((config.extra_config or {}).get("baseurl") or WECHAT_ILINK_BASE_URL).strip()
        route_tag = str((config.extra_config or {}).get("route_tag") or "").strip() or None
        await send_wechat_text_message(
            token=token,
            base_url=base_url,
            to_user_id=from_user_id,
            context_token=context_token,
            text=reply_text,
            route_tag=route_tag,
        )

        db.add(
            ChatMessage(
                agent_id=agent_id,
                user_id=platform_user_id,
                role="assistant",
                content=reply_text,
                conversation_id=session_conv_id,
            )
        )
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        await log_activity(
            agent_id,
            "chat_reply",
            f"Replied to WeChat message: {reply_text[:80]}",
            detail={"channel": "wechat", "user_text": user_text[:200], "reply": reply_text[:500]},
        )


class WeChatPollManager:
    """Manage WeChat iLink long-poll workers per agent."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._connected: dict[uuid.UUID, bool] = {}

    async def start_client(self, agent_id: uuid.UUID, stop_existing: bool = True) -> None:
        if stop_existing:
            await self.stop_client(agent_id)
        task = asyncio.create_task(self._run_client(agent_id), name=f"wechat-poll-{str(agent_id)[:8]}")
        self._tasks[agent_id] = task
        self._connected[agent_id] = False

    async def stop_client(self, agent_id: uuid.UUID) -> None:
        task = self._tasks.pop(agent_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._connected[agent_id] = False
        await self._set_connected(agent_id, False)

    async def start_all(self) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.channel_type == "wechat",
                    ChannelConfig.is_configured == True,
                )
            )
            for cfg in result.scalars().all():
                token = str((cfg.extra_config or {}).get("bot_token") or "").strip()
                if token:
                    await self.start_client(cfg.agent_id)

    async def _run_client(self, agent_id: uuid.UUID) -> None:
        retry_delay = 2
        max_retry_delay = 30
        try:
            while True:
                config = await self._load_config(agent_id)
                if not config:
                    logger.info(f"[WeChat] Channel config missing for agent {agent_id}, stopping poller")
                    return

                extra = config.extra_config or {}
                token = str(extra.get("bot_token") or "").strip()
                base_url = str(extra.get("baseurl") or WECHAT_ILINK_BASE_URL).strip()
                route_tag = str(extra.get("route_tag") or "").strip() or None
                cursor = str(extra.get("get_updates_buf") or "")

                if not token:
                    logger.info(f"[WeChat] No bot token for agent {agent_id}, stopping poller")
                    await self._set_connected(agent_id, False)
                    return

                try:
                    data = await self._fetch_updates(token=token, base_url=base_url, cursor=cursor, route_tag=route_tag)
                    self._connected[agent_id] = True
                    await self._set_connected(agent_id, True)
                    if extra.get("session_expired"):
                        await self._update_extra(agent_id, {"session_expired": False})
                    retry_delay = 2

                    new_cursor = str(data.get("get_updates_buf") or "")
                    if new_cursor and new_cursor != cursor:
                        await self._update_extra(agent_id, {"get_updates_buf": new_cursor})

                    for msg in data.get("msgs", []) or []:
                        try:
                            await _process_wechat_message(agent_id, msg, config)
                        except Exception as exc:
                            logger.error(f"[WeChat] Failed to process message for {agent_id}: {exc}")
                except WeChatSessionExpiredError:
                    logger.warning(f"[WeChat] Session expired for agent {agent_id}")
                    await self._set_connected(agent_id, False)
                    await self._update_extra(agent_id, {"get_updates_buf": "", "session_expired": True})
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._connected[agent_id] = False
                    await self._set_connected(agent_id, False)
                    logger.error(f"[WeChat] Poll error for agent {agent_id}: {exc}")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
        except asyncio.CancelledError:
            await self._set_connected(agent_id, False)
            raise

    async def _fetch_updates(self, *, token: str, base_url: str, cursor: str, route_tag: str | None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/ilink/bot/getupdates",
                headers=build_wechat_headers(token, route_tag=route_tag),
                json={
                    "get_updates_buf": cursor,
                    "base_info": {
                        "channel_version": WECHAT_CHANNEL_VERSION,
                    },
                },
            )
            data = resp.json()
            if resp.status_code >= 400:
                raise RuntimeError(f"WeChat getupdates HTTP {resp.status_code}: {str(data)[:300]}")
            ret = data.get("ret", 0)
            errcode = data.get("errcode", 0)
            if ret == -14 or errcode == -14:
                raise WeChatSessionExpiredError(data.get("errmsg") or "session expired")
            if ret not in (0, None) or errcode not in (0, None):
                raise RuntimeError(data.get("errmsg") or f"WeChat getupdates failed: ret={ret}, errcode={errcode}")
            return data

    async def _load_config(self, agent_id: uuid.UUID) -> ChannelConfig | None:
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.agent_id == agent_id,
                    ChannelConfig.channel_type == "wechat",
                )
            )
            return result.scalar_one_or_none()

    async def _update_extra(self, agent_id: uuid.UUID, updates: dict[str, Any]) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.agent_id == agent_id,
                    ChannelConfig.channel_type == "wechat",
                )
            )
            config = result.scalar_one_or_none()
            if not config:
                return
            extra = dict(config.extra_config or {})
            extra.update(updates)
            config.extra_config = extra
            await db.commit()

    async def _set_connected(self, agent_id: uuid.UUID, connected: bool) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.agent_id == agent_id,
                    ChannelConfig.channel_type == "wechat",
                )
            )
            config = result.scalar_one_or_none()
            if not config:
                return
            config.is_connected = connected
            await db.commit()


wechat_poll_manager = WeChatPollManager()
