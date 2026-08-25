"""Tenant- and Agent-scoped Feishu group target resolution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.channel_config import ChannelConfig
from app.services.channel_session import find_or_create_channel_session
from app.services.feishu_service import FeishuAPIError, feishu_service


class FeishuGroupTargetError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def sync_feishu_group_targets(
    db: AsyncSession,
    *,
    agent: Agent,
) -> int:
    """Synchronize groups currently joined by this Agent's Feishu bot."""
    config = (
        await db.execute(
            select(ChannelConfig).where(
                ChannelConfig.agent_id == agent.id,
                ChannelConfig.channel_type == "feishu",
                ChannelConfig.is_configured.is_(True),
            )
        )
    ).scalar_one_or_none()
    if config is None or not config.app_id or not config.app_secret:
        raise FeishuGroupTargetError(
            "feishu_channel_not_configured",
            "This Agent has no configured Feishu bot for group discovery.",
        )

    page_token: str | None = None
    seen_tokens: set[str] = set()
    synchronized = 0
    for _page in range(100):
        try:
            response = await feishu_service.list_bot_chats(
                config.app_id,
                config.app_secret,
                page_size=100,
                page_token=page_token,
            )
        except FeishuAPIError as exc:
            raise FeishuGroupTargetError(
                "feishu_group_directory_failed",
                f"Feishu group directory failed: {exc.user_message}",
            ) from exc
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            raise FeishuGroupTargetError(
                "feishu_group_directory_invalid",
                "Feishu group directory returned an invalid response.",
            )
        items = data.get("items", [])
        if not isinstance(items, list):
            raise FeishuGroupTargetError(
                "feishu_group_directory_invalid",
                "Feishu group directory returned invalid items.",
            )
        for item in items:
            if not isinstance(item, dict):
                continue
            chat_id = str(item.get("chat_id") or "").strip()
            chat_mode = str(item.get("chat_mode") or "group").strip()
            if not chat_id or chat_mode not in {"group", "topic"}:
                continue
            display_name = str(item.get("name") or f"Feishu Group {chat_id[:8]}").strip()
            session = await find_or_create_channel_session(
                db=db,
                agent_id=agent.id,
                user_id=agent.creator_id,
                external_conv_id=f"feishu_group_{chat_id}",
                source_channel="feishu",
                first_message_title=display_name,
                is_group=True,
                group_name=display_name,
                created_by_user_id=agent.creator_id,
            )
            if session.group_name != display_name or session.title != display_name:
                session.group_name = display_name
                session.title = display_name
            synchronized += 1
        has_more = data.get("has_more") is True
        next_token = data.get("page_token")
        if not has_more:
            break
        if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
            raise FeishuGroupTargetError(
                "feishu_group_directory_pagination_invalid",
                "Feishu group directory pagination did not advance.",
            )
        seen_tokens.add(next_token)
        page_token = next_token
    else:
        raise FeishuGroupTargetError(
            "feishu_group_directory_page_limit",
            "Feishu group directory exceeded the safe page limit.",
        )
    await db.commit()
    return synchronized


@dataclass(frozen=True)
class FeishuGroupTarget:
    session_id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    display_name: str
    chat_id: str

    def delivery_target(self) -> dict[str, object]:
        return {
            "kind": "session",
            "session_id": str(self.session_id),
            "channel_delivery": {
                "version": 1,
                "channel": "feishu",
                "target": {
                    "receive_id": self.chat_id,
                    "receive_id_type": "chat_id",
                },
            },
        }


def _chat_id(session: ChatSession) -> str:
    external_conv_id = (session.external_conv_id or "").strip()
    prefix = "feishu_group_"
    if not external_conv_id.startswith(prefix) or not external_conv_id[len(prefix):]:
        raise FeishuGroupTargetError(
            "feishu_group_target_invalid",
            "Feishu group target has no valid provider conversation identity.",
        )
    return external_conv_id[len(prefix):]


def format_feishu_group_target(session: ChatSession) -> dict[str, object]:
    _chat_id(session)
    return {
        "member_type": "group",
        "target_recipient_id": str(session.id),
        "display_name": (session.group_name or session.title or "Feishu Group").strip(),
        "provider": {"provider_type": "feishu"},
        "can_contact": True,
        "contact_tools": ["send_channel_message"],
        "unavailable_reason": None,
    }


async def resolve_feishu_group_target(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    target_recipient_id: uuid.UUID | str,
) -> FeishuGroupTarget:
    try:
        session_id = (
            target_recipient_id
            if isinstance(target_recipient_id, uuid.UUID)
            else uuid.UUID(str(target_recipient_id))
        )
    except (TypeError, ValueError) as exc:
        raise FeishuGroupTargetError(
            "invalid_target_recipient_id",
            "target_recipient_id must be a valid Directory target UUID.",
        ) from exc

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if agent is None or agent.tenant_id is None:
        raise FeishuGroupTargetError("source_agent_not_found", "Source Agent was not found.")

    session = (
        await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == agent.tenant_id,
                ChatSession.agent_id == agent.id,
                ChatSession.session_type == "group",
                ChatSession.is_group.is_(True),
                ChatSession.source_channel == "feishu",
                ChatSession.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise FeishuGroupTargetError(
            "feishu_group_target_not_found",
            "Feishu group target is unavailable or outside this Agent's Directory.",
        )
    return FeishuGroupTarget(
        session_id=session.id,
        tenant_id=session.tenant_id,
        agent_id=agent.id,
        display_name=(session.group_name or session.title or "Feishu Group").strip(),
        chat_id=_chat_id(session),
    )


__all__ = [
    "FeishuGroupTarget",
    "FeishuGroupTargetError",
    "format_feishu_group_target",
    "resolve_feishu_group_target",
    "sync_feishu_group_targets",
]
