"""DAO for activity logs and conversation summaries."""

import re
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.dao.base import BaseDAO
from app.models.activity_log import AgentActivityLog
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.participant import Participant
from app.models.user import User


class ActivityDAO(BaseDAO[AgentActivityLog]):
    """Read-optimized activity and conversation accessors."""

    def __init__(self) -> None:
        super().__init__(AgentActivityLog)

    async def list_agent_activity(self, *, agent_id: Any, limit: int) -> list[AgentActivityLog]:
        """Return recent activity rows for an agent."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(AgentActivityLog)
                .where(AgentActivityLog.agent_id == agent_id)
                .order_by(AgentActivityLog.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def list_conversation_summaries(self, *, agent_id: Any) -> list[dict[str, Any]]:
        """Build conversation summaries using batched queries instead of per-row lookups."""
        async with self.session(readonly=True) as db:
            conversations: list[dict[str, Any]] = []

            web_stats = (
                select(
                    ChatMessage.user_id.label("user_id"),
                    func.max(ChatMessage.created_at).label("last_at"),
                    func.count(ChatMessage.id).label("cnt"),
                )
                .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id.like("web_%"))
                .group_by(ChatMessage.user_id)
                .subquery()
            )
            web_last_ranked = (
                select(
                    ChatMessage.user_id.label("user_id"),
                    ChatMessage.content.label("content"),
                    func.row_number()
                    .over(partition_by=ChatMessage.user_id, order_by=ChatMessage.created_at.desc())
                    .label("rn"),
                )
                .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id.like("web_%"))
                .subquery()
            )
            web_result = await db.execute(
                select(
                    web_stats.c.user_id,
                    web_stats.c.last_at,
                    web_stats.c.cnt,
                    User.display_name,
                    web_last_ranked.c.content,
                )
                .outerjoin(User, User.id == web_stats.c.user_id)
                .outerjoin(
                    web_last_ranked,
                    and_(web_last_ranked.c.user_id == web_stats.c.user_id, web_last_ranked.c.rn == 1),
                )
            )
            for user_id, last_at, cnt, display_name, last_content in web_result.all():
                conversations.append(
                    {
                        "conv_id": f"web_{user_id}",
                        "partner_type": "user",
                        "partner_id": str(user_id),
                        "partner_name": f"👤 {display_name or '未知用户'}",
                        "last_message": (last_content or "")[:80],
                        "message_count": cnt,
                        "last_at": last_at.isoformat() if last_at else None,
                    }
                )

            for prefix, icon, label, partner_type in [
                ("feishu_", "📱", "飞书用户", "feishu"),
                ("slack_", "💬", "Slack", "slack"),
                ("discord_", "🎮", "Discord", "discord"),
            ]:
                channel_stats = (
                    select(
                        ChatMessage.conversation_id.label("conv_id"),
                        func.max(ChatMessage.created_at).label("last_at"),
                        func.count(ChatMessage.id).label("cnt"),
                    )
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id.like(f"{prefix}%"))
                    .group_by(ChatMessage.conversation_id)
                    .subquery()
                )
                channel_last_ranked = (
                    select(
                        ChatMessage.conversation_id.label("conv_id"),
                        ChatMessage.content.label("content"),
                        func.row_number()
                        .over(partition_by=ChatMessage.conversation_id, order_by=ChatMessage.created_at.desc())
                        .label("rn"),
                    )
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id.like(f"{prefix}%"))
                    .subquery()
                )
                channel_result = await db.execute(
                    select(
                        channel_stats.c.conv_id,
                        channel_stats.c.last_at,
                        channel_stats.c.cnt,
                        channel_last_ranked.c.content,
                    ).outerjoin(
                        channel_last_ranked,
                        and_(channel_last_ranked.c.conv_id == channel_stats.c.conv_id, channel_last_ranked.c.rn == 1),
                    )
                )
                for conv_id, last_at, cnt, last_content in channel_result.all():
                    if prefix == "feishu_":
                        display_name = "👥 飞书群聊" if not conv_id.startswith("feishu_p2p_") else f"{icon} {label}"
                    else:
                        parts = conv_id.split("_", 2)
                        channel_part = parts[1] if len(parts) > 1 else conv_id
                        display_name = (
                            f"{icon} {label} #{channel_part}" if channel_part != "dm" else f"{icon} {label} DM"
                        )
                    conversations.append(
                        {
                            "conv_id": conv_id,
                            "partner_type": partner_type,
                            "partner_id": conv_id,
                            "partner_name": display_name,
                            "last_message": (last_content or "")[:80],
                            "message_count": cnt,
                            "last_at": last_at.isoformat() if last_at else None,
                        }
                    )

            session_stats = (
                select(
                    ChatMessage.conversation_id.label("conv_id"),
                    func.count(ChatMessage.id).label("cnt"),
                    func.max(ChatMessage.created_at).label("last_at"),
                )
                .group_by(ChatMessage.conversation_id)
                .subquery()
            )
            session_last_ranked = (
                select(
                    ChatMessage.conversation_id.label("conv_id"),
                    ChatMessage.content.label("content"),
                    func.row_number()
                    .over(partition_by=ChatMessage.conversation_id, order_by=ChatMessage.created_at.desc())
                    .label("rn"),
                ).subquery()
            )
            agent_session_result = await db.execute(
                select(
                    ChatSession.id,
                    ChatSession.agent_id,
                    ChatSession.peer_agent_id,
                    Agent.name,
                    session_stats.c.cnt,
                    session_stats.c.last_at,
                    session_last_ranked.c.content,
                )
                .outerjoin(
                    Agent,
                    Agent.id
                    == func.coalesce(
                        func.nullif(ChatSession.peer_agent_id, agent_id),
                        ChatSession.agent_id,
                    ),
                )
                .outerjoin(session_stats, session_stats.c.conv_id == func.cast(ChatSession.id, ChatMessage.conversation_id.type))
                .outerjoin(
                    session_last_ranked,
                    and_(
                        session_last_ranked.c.conv_id == func.cast(ChatSession.id, ChatMessage.conversation_id.type),
                        session_last_ranked.c.rn == 1,
                    ),
                )
                .where(
                    ChatSession.source_channel == "agent",
                    or_(ChatSession.agent_id == agent_id, ChatSession.peer_agent_id == agent_id),
                )
            )
            for session_id, sess_agent_id, peer_agent_id, partner_name, cnt, last_at, last_content in agent_session_result.all():
                partner_id = peer_agent_id if sess_agent_id == agent_id else sess_agent_id
                conversations.append(
                    {
                        "conv_id": str(session_id),
                        "partner_type": "agent",
                        "partner_id": str(partner_id),
                        "partner_name": f"🤖 {partner_name or '未知数字员工'}",
                        "last_message": (last_content or "")[:80],
                        "message_count": cnt or 0,
                        "last_at": last_at.isoformat() if last_at else None,
                    }
                )

            conversations.sort(key=lambda c: c["last_at"] or "", reverse=True)
            return conversations

    async def list_conversation_messages(self, *, agent_id: Any, conv_id: str, limit: int) -> list[dict[str, Any]]:
        """Return chat history messages and batch-load external participant names."""
        async with self.session(readonly=True) as db:
            messages: list[dict[str, Any]] = []
            if conv_id.startswith(("web_", "feishu_", "slack_", "discord_")):
                result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == conv_id)
                    .order_by(ChatMessage.created_at.asc())
                    .limit(limit)
                )
                for message in result.scalars().all():
                    content = message.content
                    if content.startswith("[发送者:"):
                        content = re.sub(r"^\[发送者:[^\]]*\]\s*", "", content)
                    messages.append(
                        {
                            "id": str(message.id),
                            "role": message.role,
                            "content": content,
                            "created_at": message.created_at.isoformat() if message.created_at else None,
                        }
                    )
                return messages

            if conv_id.startswith("agent_") or len(conv_id) == 36:
                result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conv_id)
                    .order_by(ChatMessage.created_at.asc())
                    .limit(limit)
                )
                rows = list(result.scalars().all())
                participant_ids = [message.participant_id for message in rows if message.participant_id]
                participant_names: dict[Any, str] = {}
                if participant_ids:
                    participant_result = await db.execute(
                        select(Participant.id, Participant.display_name).where(Participant.id.in_(participant_ids))
                    )
                    participant_names = {pid: display_name or "未知" for pid, display_name in participant_result.all()}

                for message in rows:
                    sender_name = participant_names.get(message.participant_id, "未知") if message.participant_id else "未知"
                    messages.append(
                        {
                            "id": str(message.id),
                            "role": message.role,
                            "sender_name": sender_name,
                            "content": message.content,
                            "created_at": message.created_at.isoformat() if message.created_at else None,
                        }
                    )

            return messages


activity_dao = ActivityDAO()
