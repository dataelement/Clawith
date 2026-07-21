"""Transaction-scoped lifecycle helpers for direct chat sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ChatMessage
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services.agent_runtime.persistence import enqueue_cancel
from app.services.participant_identity import get_or_create_user_participant


_DIRECT_SESSION_TYPE = "direct"


@dataclass(frozen=True, slots=True)
class DirectSessionDeletion:
    """The direct-session mutations staged in the caller's transaction."""

    session: ChatSession
    replacement: ChatSession | None
    cancelled_run_ids: tuple[uuid.UUID, ...]


def _direct_scope_key(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    return f"direct:{tenant_id}:{agent_id}:{user_id}"


async def _lock_direct_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Serialize primary lifecycle changes for one direct conversation scope."""
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(_direct_scope_key(tenant_id, agent_id, user_id), 0)
            )
        )
    )


def _active_direct_sessions(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
):
    return (
        ChatSession.tenant_id == tenant_id,
        ChatSession.agent_id == agent_id,
        ChatSession.user_id == user_id,
        ChatSession.session_type == _DIRECT_SESSION_TYPE,
        ChatSession.deleted_at.is_(None),
    )


def _best_active_direct_session_statement(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
):
    return (
        select(ChatSession)
        .where(*_active_direct_sessions(tenant_id, agent_id, user_id))
        .order_by(
            ChatSession.last_message_at.desc().nulls_last(),
            ChatSession.created_at.desc(),
            ChatSession.id.desc(),
        )
        .execution_options(populate_existing=True)
        .limit(1)
    )


async def get_primary_direct_session(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSession | None:
    """Return the active primary direct session for an exact tenant scope."""
    result = await db.execute(
        select(ChatSession)
        .where(
            *_active_direct_sessions(tenant_id, agent_id, user_id),
            ChatSession.is_primary.is_(True),
        )
        .execution_options(populate_existing=True)
        .limit(1)
    )
    return result.scalar_one_or_none()


def _new_direct_session(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    created_by_participant_id: uuid.UUID,
    title: str | None,
    is_primary: bool,
    now: datetime,
) -> ChatSession:
    return ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type=_DIRECT_SESSION_TYPE,
        group_id=None,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=created_by_participant_id,
        title=title or f"Session {now.strftime('%m-%d %H:%M')}",
        source_channel="web",
        is_group=False,
        is_primary=is_primary,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )


async def ensure_primary_direct_session(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    created_by_participant_id: uuid.UUID,
) -> ChatSession:
    """Reuse, promote, or create the primary direct session without committing."""
    await _lock_direct_scope(db, tenant_id, agent_id, user_id)

    primary = await get_primary_direct_session(db, tenant_id, agent_id, user_id)
    if primary is not None:
        return primary

    result = await db.execute(
        _best_active_direct_session_statement(tenant_id, agent_id, user_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.is_primary = True
        existing.updated_at = datetime.now(UTC)
        await db.flush()
        return existing

    now = datetime.now(UTC)
    session = _new_direct_session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=created_by_participant_id,
        title=None,
        is_primary=True,
        now=now,
    )
    db.add(session)
    await db.flush()
    return session


async def create_direct_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    created_by_participant_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """Create a direct session; only the first active session becomes primary."""
    await _lock_direct_scope(db, tenant_id, agent_id, user_id)

    primary = await get_primary_direct_session(db, tenant_id, agent_id, user_id)
    existing = None
    if primary is None:
        result = await db.execute(
            _best_active_direct_session_statement(tenant_id, agent_id, user_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.is_primary = True
            existing.updated_at = datetime.now(UTC)

    now = datetime.now(UTC)
    session = _new_direct_session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=created_by_participant_id,
        title=title,
        is_primary=primary is None and existing is None,
        now=now,
    )
    db.add(session)
    await db.flush()
    return session


async def _runs_cancelled_by_session_deletion(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> list[AgentRun]:
    roots = (
        select(AgentRun.id.label("run_id"))
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.session_id == session_id,
            AgentRun.run_kind.in_(("foreground", "orchestration")),
        )
        .cte("session_cancel_tree", recursive=True)
    )
    delegated_descendants = (
        select(AgentRun.id.label("run_id"))
        .join(roots, AgentRun.parent_run_id == roots.c.run_id)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.run_kind == "delegated",
        )
    )
    cancel_tree = roots.union_all(delegated_descendants)
    result = await db.execute(
        select(AgentRun)
        .join(cancel_tree, AgentRun.id == cancel_tree.c.run_id)
        .where(AgentRun.tenant_id == tenant_id)
        .order_by(AgentRun.created_at, AgentRun.id)
    )
    return list(result.scalars().all())


async def enqueue_session_deletion_cancels(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> tuple[uuid.UUID, ...]:
    """Cancel foreground collaboration rooted in a deleted ChatSession."""
    runs = await _runs_cancelled_by_session_deletion(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    for run in runs:
        await enqueue_cancel(
            db,
            tenant_id=tenant_id,
            run_id=run.id,
            idempotency_key=f"session-delete:{session_id}:run:{run.id}",
            reason="session_deleted",
            actor_user_id=actor_user_id,
        )
    return tuple(run.id for run in runs)


async def soft_delete_direct_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> DirectSessionDeletion | None:
    """Soft-delete a direct session, repair primary, and enqueue Runtime cancels."""
    await _lock_direct_scope(db, tenant_id, agent_id, user_id)
    result = await db.execute(
        select(ChatSession)
        .where(
            *_active_direct_sessions(tenant_id, agent_id, user_id),
            ChatSession.id == session_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    session = result.scalar_one_or_none()
    if session is None:
        return None

    was_primary = bool(session.is_primary)
    now = datetime.now(UTC)
    session.deleted_at = now
    session.updated_at = now
    await db.flush()

    replacement = None
    if was_primary:
        replacement_result = await db.execute(
            _best_active_direct_session_statement(tenant_id, agent_id, user_id)
        )
        replacement = replacement_result.scalar_one_or_none()
        if replacement is not None:
            replacement.is_primary = True
            replacement.updated_at = now
            await db.flush()

    cancelled_run_ids = await enqueue_session_deletion_cancels(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        actor_user_id=actor_user_id,
    )

    return DirectSessionDeletion(
        session=session,
        replacement=replacement,
        cancelled_run_ids=cancelled_run_ids,
    )


async def get_primary_platform_session(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSession | None:
    """Compatibility wrapper for callers that do not yet pass tenant identity."""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.tenant_id is None:
        return None
    return await get_primary_direct_session(db, agent.tenant_id, agent_id, user_id)


async def ensure_primary_platform_session(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSession:
    """Compatibility wrapper that resolves tenant and creator Participant first."""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.tenant_id is None:
        raise ValueError("agent must belong to a tenant")

    user_result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == agent.tenant_id,
            User.is_active.is_(True),
        )
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise ValueError("user must be active in the agent tenant")
    participant = await get_or_create_user_participant(
        db,
        user.id,
        user.display_name,
        user.avatar_url,
    )
    return await ensure_primary_direct_session(
        db,
        agent.tenant_id,
        agent_id,
        user_id,
        participant.id,
    )


async def save_tool_call_log(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: str,
    tool_name: str,
    arguments: dict | None,
    result: str,
    status: str = "done",
    tool_call_id: str | None = None,
    reasoning_content: str | None = None,
) -> None:
    """Save a tool call execution log into chat history as a ChatMessage."""
    if not conversation_id:
        return
    import json
    from app.database import async_session
    from loguru import logger

    payload = {
        "name": tool_name,
        "args": arguments or {},
        "status": status,
        "result": str(result) if result is not None else "",
        "tool_call_id": tool_call_id,
        "reasoning_content": reasoning_content,
    }

    try:
        async with async_session() as db:
            db.add(ChatMessage(
                agent_id=agent_id,
                user_id=user_id,
                role="tool_call",
                content=json.dumps(payload, ensure_ascii=False, default=str),
                conversation_id=conversation_id,
            ))
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to save tool call log: {e}")
