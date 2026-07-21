"""Durable Runtime intake for non-Web chat adapters."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.agent_runtime.chat_intake import (
    ChatRuntimeIntake,
    enqueue_chat_runtime,
)
from app.services.agent_runtime.run_state_reader import (
    RunStateReadError,
    RunStateReader,
    open_run_state_reader,
)


class ChannelChatRuntimeError(RuntimeError):
    """A channel cannot resolve the durable result for its accepted Run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def channel_message_id(
    agent_id: uuid.UUID,
    source_channel: str,
    external_event_id: str | None,
) -> uuid.UUID:
    """Map a provider event identity to one retry-safe ChatMessage ID."""
    normalized_event = (external_event_id or "").strip()
    if not normalized_event:
        return uuid.uuid4()
    return uuid.uuid5(
        agent_id,
        f"channel-message:{source_channel.strip()}:{normalized_event}",
    )


async def _waiting_resume(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    run_state_reader: RunStateReader,
) -> tuple[uuid.UUID, str] | None:
    """Read the active channel wait from the authoritative LangGraph checkpoint."""
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.agent_id == agent_id,
            AgentRun.session_id == session_id,
            AgentRun.origin_user_id == user_id,
            AgentRun.source_type == "chat",
            AgentRun.run_kind == "foreground",
            AgentRun.runtime_type == "langgraph",
            AgentRun.runtime_thread_id == str(session_id),
            AgentRun.lane_held.is_(True),
        )
        .order_by(AgentRun.created_at, AgentRun.id)
        .limit(2)
    )
    holders = list(result.scalars().all())
    if not holders:
        return None
    if len(holders) != 1:
        raise ChannelChatRuntimeError(
            "multiple_channel_lane_holders",
            "Channel Chat Session has multiple active Runtime lane holders",
        )
    run = holders[0]
    try:
        view = await run_state_reader.get_run_state(tenant_id, run.id)
    except RunStateReadError as exc:
        raise ChannelChatRuntimeError(exc.code, str(exc)) from exc
    if (
        view.run_id != run.id
        or view.thread_id != str(session_id)
        or view.session_id != session_id
        or view.execution_status != "waiting_user"
    ):
        return None
    correlation_id = view.waiting_correlation_id
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise ChannelChatRuntimeError(
            "channel_wait_correlation_missing",
            "Waiting channel Run has no stable resume correlation",
        )
    return run.id, correlation_id.strip()


async def enqueue_channel_chat_runtime(
    db: AsyncSession,
    *,
    agent: Agent,
    user: User,
    session: ChatSession,
    model: LLMModel | None,
    content: str,
    source_channel: str,
    message_id: uuid.UUID,
    channel_delivery_target: dict,
    display_content: str = "",
    file_name: str = "",
) -> ChatRuntimeIntake:
    """Atomically attach a channel message to a new or waiting Chat Run."""
    if agent.tenant_id is None or model is None:
        raise ChannelChatRuntimeError(
            "channel_model_unavailable",
            "Channel Agent has no available model",
        )
    async with open_run_state_reader(db) as run_state_reader:
        resume = await _waiting_resume(
            db,
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            session_id=session.id,
            user_id=user.id,
            run_state_reader=run_state_reader,
        )
        intake = await enqueue_chat_runtime(
            db,
            agent=agent,
            user=user,
            session=session,
            model=model,
            content=content,
            display_content=display_content,
            file_name=file_name,
            message_id=message_id,
            resume_run_id=resume[0] if resume is not None else None,
            resume_correlation_id=resume[1] if resume is not None else None,
            source_channel=source_channel,
            channel_delivery_target=channel_delivery_target,
            run_state_reader=run_state_reader,
        )
    if intake is None:
        raise ChannelChatRuntimeError(
            "channel_runtime_disabled",
            "Unified Agent Runtime is not enabled for this channel",
        )
    return intake


__all__ = [
    "ChannelChatRuntimeError",
    "channel_message_id",
    "enqueue_channel_chat_runtime",
]
