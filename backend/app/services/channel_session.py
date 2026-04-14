"""Shared helpers for external channel sessions and shared public transcript reads."""
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession


async def find_or_create_channel_session(
    db: AsyncSession,
    agent_id: _uuid.UUID,
    user_id: _uuid.UUID,
    external_conv_id: str,
    source_channel: str,
    first_message_title: str,
    is_group: bool = False,
    group_name: str | None = None,
) -> ChatSession:
    """Find an existing ChatSession by (agent_id, external_conv_id), or create one.

    Relies on the UNIQUE constraint on (agent_id, external_conv_id) in the DB.

    Args:
        is_group: True for group chat sessions (Feishu group, Slack channel, etc.).
                  Group sessions keep user_id as the agent creator (placeholder) and
                  are excluded from the user's "mine" session list.
        group_name: Display name for group sessions (e.g. IM group/channel name).
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.agent_id == agent_id,
            ChatSession.external_conv_id == external_conv_id,
        )
    )
    session = result.scalar_one_or_none()

    if session is None:
        now = datetime.now(timezone.utc)
        session = ChatSession(
            agent_id=agent_id,
            user_id=user_id,
            title=group_name[:40] if (is_group and group_name) else first_message_title[:40],
            source_channel=source_channel,
            external_conv_id=external_conv_id,
            is_group=is_group,
            group_name=group_name,
            created_at=now,
        )
        db.add(session)
        await db.flush()  # populate session.id
    else:
        # For P2P sessions: re-attribute to the correct user
        # (fixes legacy sessions stored under creator_id)
        if not session.is_group and session.user_id != user_id:
            session.user_id = user_id

        # For group sessions: update group_name if it changed
        if session.is_group and group_name and session.group_name != group_name:
            session.group_name = group_name
            session.title = group_name[:40]

    return session


def _normalize_shared_channel_history(
    messages: list,
    current_agent_id: _uuid.UUID,
    user_names: dict[_uuid.UUID, str],
    agent_names: dict[_uuid.UUID, str],
    limit: int,
) -> list[dict]:
    """Normalize raw group messages into prompt-safe shared transcript entries.

    - Human messages remain `user` messages with explicit speaker labels.
    - The current agent's prior replies remain `assistant` messages.
    - Other agents' prior replies are converted into visible public transcript
      lines as `user` messages so they are not mistaken for the current
      assistant's own prior output.
    - Duplicate public human messages from multiple per-agent sessions are
      deduplicated within a short time bucket.
    """
    seen: set[tuple] = set()
    external_message_positions: dict[str, int] = {}
    normalized: list[dict] = []

    for message in messages:
        if message.role not in ("user", "assistant"):
            continue

        if message.role == "assistant":
            speaker_name = agent_names.get(message.agent_id, "未知智能体")
            if message.agent_id == current_agent_id:
                entry = {"role": "assistant", "content": message.content}
            else:
                entry = {"role": "user", "content": f"[其他智能体 {speaker_name}] {message.content}"}
            dedupe_speaker = ("assistant", message.agent_id)
            entry_priority = 2
        else:
            speaker_name = user_names.get(message.user_id, "群成员")
            entry = {"role": "user", "content": f"[群成员 {speaker_name}] {message.content}"}
            dedupe_speaker = ("user", message.user_id)
            entry_priority = 1

        external_message_id = getattr(message, "external_message_id", None)
        created_at = getattr(message, "created_at", None)
        bucket = int(created_at.timestamp() // 10) if created_at else None
        if external_message_id:
            existing_pos = external_message_positions.get(external_message_id)
            if existing_pos is None:
                external_message_positions[external_message_id] = len(normalized)
                normalized.append({**entry, "_priority": entry_priority})
            else:
                if entry_priority > normalized[existing_pos].get("_priority", 0):
                    normalized[existing_pos] = {**entry, "_priority": entry_priority}
            continue
        else:
            dedupe_key = (dedupe_speaker, entry["content"], bucket)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append({**entry, "_priority": entry_priority})

    if len(normalized) > limit:
        normalized = normalized[-limit:]
    return [{"role": entry["role"], "content": entry["content"]} for entry in normalized]


async def load_shared_channel_history(
    db: AsyncSession,
    *,
    current_agent_id: _uuid.UUID,
    current_tenant_id: _uuid.UUID | None,
    external_conv_id: str,
    source_channel: str,
    limit: int = 100,
) -> list[dict]:
    """Load shared public history across all sessions of the same external chat.

    This is intentionally scoped to public channel context. Persistence remains
    per-agent, but prompt construction for shared channels can read a unified
    transcript keyed by `(source_channel, external_conv_id)`.
    """
    from app.models.agent import Agent
    from app.models.audit import ChatMessage
    from app.models.user import User

    if not current_tenant_id:
        return []

    sessions_result = await db.execute(
        select(ChatSession.id)
        .join(Agent, Agent.id == ChatSession.agent_id)
        .where(
            ChatSession.external_conv_id == external_conv_id,
            ChatSession.source_channel == source_channel,
            ChatSession.is_group == True,
            Agent.tenant_id == current_tenant_id,
        )
    )
    session_ids = [str(row[0]) for row in sessions_result.fetchall()]
    if not session_ids:
        return []

    raw_limit = max(limit * 4, limit)
    messages_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id.in_(session_ids))
        .order_by(ChatMessage.created_at.desc())
        .limit(raw_limit)
    )
    messages = list(reversed(messages_result.scalars().all()))
    if not messages:
        return []

    user_ids = {m.user_id for m in messages if getattr(m, "user_id", None)}
    agent_ids = {m.agent_id for m in messages if getattr(m, "agent_id", None)}

    user_names: dict[_uuid.UUID, str] = {}
    if user_ids:
        user_result = await db.execute(
            select(User.id, User.display_name, User.username).where(User.id.in_(user_ids))
        )
        for user_id, display_name, username in user_result.fetchall():
            user_names[user_id] = display_name or username or "群成员"

    agent_names: dict[_uuid.UUID, str] = {}
    if agent_ids:
        agent_result = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
        for agent_id, name in agent_result.fetchall():
            agent_names[agent_id] = name or "未知智能体"

    return _normalize_shared_channel_history(messages, current_agent_id, user_names, agent_names, limit)
