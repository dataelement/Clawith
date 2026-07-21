"""Tenant-scoped Direct Chat session management endpoints."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, and_, cast, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import AuditLog, ChatMessage
from app.models.chat_session import ChatSession
from app.models.participant import Participant
from app.models.user import Identity, User
from app.services.chat_session_service import (
    create_direct_session,
    soft_delete_direct_session,
)
from app.services.agent_runtime.run_state_reader import (
    RunStateReadError,
    open_run_state_reader as _open_run_state_reader,
)
from app.services.agent_runtime.tool_execution import (
    ToolExecutionError,
    reconcile_unknown_tool_execution,
)
from app.services.participant_identity import get_or_create_user_participant

router = APIRouter(prefix="/api/agents", tags=["chat-sessions"])


def _can_view_all_agent_chat_sessions(user: User, agent: Agent) -> bool:
    """Admins and the agent creator may inspect other users' direct sessions."""
    return user.role in ("platform_admin", "org_admin", "agent_admin") or str(agent.creator_id) == str(user.id)


def _require_tenant_id(user: User) -> uuid.UUID:
    tenant_id = getattr(user, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="A tenant is required for chat sessions")
    return tenant_id


def _active_direct_filters(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
):
    return (
        ChatSession.tenant_id == tenant_id,
        ChatSession.agent_id == agent_id,
        ChatSession.session_type == "direct",
        ChatSession.deleted_at.is_(None),
    )


def _active_agent_session_filters(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
):
    """Scope the legacy Agent session surface to active associated sessions."""
    return (
        ChatSession.tenant_id == tenant_id,
        ChatSession.deleted_at.is_(None),
        or_(
            ChatSession.agent_id == agent_id,
            and_(
                ChatSession.session_type == "a2a",
                ChatSession.peer_agent_id == agent_id,
            ),
        ),
    )


def _is_a2a_session(session: ChatSession) -> bool:
    return session.session_type == "a2a"


def _is_group_session(session: ChatSession) -> bool:
    return session.session_type == "group"


async def _check_direct_agent_access(
    db: AsyncSession,
    current_user: User,
    agent_id: uuid.UUID,
) -> tuple[Agent, uuid.UUID]:
    tenant_id = _require_tenant_id(current_user)
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if agent.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="No access to this agent")
    return agent, tenant_id


def _authorize_session_owner(current_user: User, agent: Agent, session: ChatSession) -> None:
    if str(session.user_id) != str(current_user.id) and not _can_view_all_agent_chat_sessions(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized")


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    source_channel: str = "web"
    title: str
    created_at: str
    last_message_at: str | None = None
    message_count: int = 0
    unread_count: int = 0
    is_primary: bool = False
    peer_agent_id: str | None = None
    peer_agent_name: str | None = None
    participant_type: str = "user"
    is_group: bool = False
    group_name: str | None = None


class CreateSessionIn(BaseModel):
    title: str | None = None


class PatchSessionIn(BaseModel):
    title: str


class ActiveRunOut(BaseModel):
    """Minimal persisted runtime identity needed to resume or cancel safely."""

    run_id: str
    thread_id: str
    session_id: str
    status: str
    waiting_type: str | None = None
    waiting_reason: str | None = None
    correlation_id: str | None = None
    model_step_count: int = 0
    can_resume: bool = False
    can_cancel: bool = False
    pending_tool_reconciliations: list["PendingToolReconciliationOut"] = Field(
        default_factory=list
    )


class PendingToolReconciliationOut(BaseModel):
    execution_id: str
    tool_call_id: str
    tool_name: str
    result_summary: str | None = None
    error_code: str | None = None
    can_reconcile: bool = False


class ReconcileToolExecutionIn(BaseModel):
    outcome: Literal["applied", "not_applied"]
    correlation_id: str
    note: str


class ReconcileToolExecutionOut(BaseModel):
    execution_id: str
    status: Literal["succeeded", "failed"]
    result_summary: str


class SessionRuntimeStateOut(BaseModel):
    active_run: ActiveRunOut | None = None


def _session_out(
    session: ChatSession,
    *,
    username: str | None = None,
    message_count: int = 0,
    unread_count: int = 0,
    peer_agent_id: uuid.UUID | None = None,
    peer_agent_name: str | None = None,
    participant_type: str = "user",
    is_group: bool = False,
    group_name: str | None = None,
) -> SessionOut:
    return SessionOut(
        id=str(session.id),
        agent_id=str(session.agent_id) if session.agent_id else None,
        user_id=str(session.user_id) if session.user_id else None,
        username=username,
        source_channel=session.source_channel,
        title=session.title,
        created_at=session.created_at.isoformat(),
        last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
        message_count=message_count,
        unread_count=unread_count,
        is_primary=bool(session.is_primary),
        peer_agent_id=str(peer_agent_id) if peer_agent_id else None,
        peer_agent_name=peer_agent_name,
        participant_type=participant_type,
        is_group=is_group,
        group_name=group_name,
    )


@router.get("/{agent_id}/sessions")
async def list_sessions(
    agent_id: uuid.UUID,
    scope: Annotated[str, Query(description="'mine' or 'all'")] = "mine",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List active sessions on the legacy Agent session surface."""
    agent, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    if scope not in {"mine", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'mine' or 'all'")
    if scope == "all" and not _can_view_all_agent_chat_sessions(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized to view all sessions")

    if scope == "mine":
        session_filters = _active_direct_filters(tenant_id, agent_id)
        session_query = select(ChatSession).where(
            *session_filters,
            ChatSession.user_id == current_user.id,
        )
    else:
        session_filters = _active_agent_session_filters(tenant_id, agent_id)
        session_query = select(ChatSession).where(*session_filters)
    result = await db.execute(
        session_query.order_by(
            ChatSession.last_message_at.desc().nulls_last(),
            ChatSession.created_at.desc(),
            ChatSession.id.desc(),
        )
    )
    sessions = list(result.scalars().all())
    if not sessions:
        return []

    session_ids = [session.id for session in sessions]
    conversation_ids = [str(session_id) for session_id in session_ids]
    count_result = await db.execute(
        select(ChatMessage.conversation_id, func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.conversation_id == cast(ChatSession.id, String))
        .where(
            *session_filters,
            ChatSession.id.in_(session_ids),
            ChatMessage.conversation_id.in_(conversation_ids),
        )
        .group_by(ChatMessage.conversation_id)
    )
    message_counts = {row[0]: int(row[1] or 0) for row in count_result.all()}

    unread_result = await db.execute(
        select(ChatSession.id, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.conversation_id == cast(ChatSession.id, String))
        .where(
            *_active_direct_filters(tenant_id, agent_id),
            ChatSession.id.in_(session_ids),
            ChatSession.user_id == current_user.id,
            ChatMessage.role.in_(("assistant", "system", "tool_call")),
            ChatMessage.created_at
            > func.coalesce(
                ChatSession.last_read_at_by_user,
                datetime(1970, 1, 1, tzinfo=UTC),
            ),
        )
        .group_by(ChatSession.id)
    )
    unread_counts = {str(row[0]): int(row[1] or 0) for row in unread_result.all()}

    user_names: dict[str, str] = {}
    agent_names: dict[str, str] = {}
    if scope == "all":
        user_ids = list(
            {
                session.user_id
                for session in sessions
                if session.user_id and not _is_a2a_session(session) and not _is_group_session(session)
            }
        )
        if user_ids:
            user_result = await db.execute(
                select(User.id, func.coalesce(User.display_name, Identity.username))
                .join(Identity, User.identity_id == Identity.id)
                .where(User.tenant_id == tenant_id, User.id.in_(user_ids))
            )
            user_names = {str(row[0]): row[1] or "Unknown" for row in user_result.all()}

        a2a_agent_ids = {
            candidate_id
            for session in sessions
            if _is_a2a_session(session)
            for candidate_id in (session.agent_id, session.peer_agent_id)
            if candidate_id is not None
        }
        if a2a_agent_ids:
            agent_result = await db.execute(
                select(Agent.id, Agent.name).where(
                    Agent.tenant_id == tenant_id,
                    Agent.id.in_(a2a_agent_ids),
                )
            )
            agent_names = {str(row[0]): row[1] or "Agent" for row in agent_result.all()}

    output = []
    for session in sessions:
        count = message_counts.get(str(session.id), 0)
        if count == 0:
            continue
        username = None
        peer_agent_id = None
        peer_agent_name = None
        participant_type = "user"
        is_group = False
        group_name = None
        if scope == "all" and _is_a2a_session(session):
            participant_type = "agent"
            peer_agent_id = session.peer_agent_id if session.agent_id == agent_id else session.agent_id
            peer_agent_name = agent_names.get(str(peer_agent_id), "Agent")
            primary_name = agent_names.get(str(session.agent_id), "Agent")
            stored_peer_name = agent_names.get(str(session.peer_agent_id), "Agent")
            username = f"Agent {primary_name} - {stored_peer_name}"
        elif scope == "all" and _is_group_session(session):
            participant_type = "group"
            is_group = True
            group_name = session.group_name
            username = session.group_name or session.title or "Group Chat"
        elif scope == "all":
            username = user_names.get(str(session.user_id), "Unknown")

        output.append(
            _session_out(
                session,
                username=username,
                message_count=count,
                unread_count=unread_counts.get(str(session.id), 0),
                peer_agent_id=peer_agent_id,
                peer_agent_name=peer_agent_name,
                participant_type=participant_type,
                is_group=is_group,
                group_name=group_name,
            )
        )
    return output


@router.post("/{agent_id}/sessions", status_code=201)
async def create_session(
    agent_id: uuid.UUID,
    body: CreateSessionIn = CreateSessionIn(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a direct session for the active current-tenant User."""
    _, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    user_result = await db.execute(
        select(User).where(
            User.id == current_user.id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=403, detail="Current user is not active in this tenant")

    participant = await get_or_create_user_participant(
        db,
        user.id,
        user.display_name,
        user.avatar_url,
    )
    session = await create_direct_session(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user.id,
        created_by_participant_id=participant.id,
        title=body.title,
    )
    await db.commit()
    await db.refresh(session)
    return _session_out(session)


@router.get(
    "/{agent_id}/sessions/{session_id}/runtime-state",
    response_model=SessionRuntimeStateOut,
)
async def get_session_runtime_state(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeStateOut:
    """Return the one exact Direct Chat lane holder, if one exists."""
    _agent, tenant_id = await _check_direct_agent_access(
        db,
        current_user,
        agent_id,
    )
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == current_user.id,
            ChatSession.session_type == "direct",
            ChatSession.group_id.is_(None),
            ChatSession.source_channel == "web",
            ChatSession.deleted_at.is_(None),
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    lane_key = f"direct_chat_thread:{tenant_id}:{session.id}"
    holders_result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.agent_id == agent_id,
            AgentRun.session_id == session.id,
            AgentRun.origin_user_id == current_user.id,
            AgentRun.source_type == "chat",
            AgentRun.run_kind == "foreground",
            AgentRun.runtime_type == "langgraph",
            AgentRun.runtime_thread_id == str(session.id),
            AgentRun.scheduling_lane_key == lane_key,
            AgentRun.lane_held.is_(True),
        )
        .order_by(AgentRun.created_at, AgentRun.id)
        .limit(2)
    )
    holders = list(holders_result.scalars().all())
    if not holders:
        return SessionRuntimeStateOut(active_run=None)
    if len(holders) != 1:
        raise HTTPException(
            status_code=409,
            detail="multiple_direct_session_lane_holders",
        )
    run = holders[0]

    try:
        async with _open_run_state_reader(db) as reader:
            view = await reader.get_run_state(tenant_id, run.id)
    except RunStateReadError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc

    if (
        view.tenant_id != tenant_id
        or view.run_id != run.id
        or view.thread_id != str(session.id)
        or view.session_id != session.id
        or view.source_type != "chat"
        or view.run_kind != "foreground"
        or view.runtime_type != "langgraph"
        or view.execution_status is None
    ):
        raise HTTPException(status_code=409, detail="runtime_state_scope_mismatch")

    waiting_type = view.waiting_type
    correlation_id = view.waiting_correlation_id
    if view.execution_status == "waiting_user":
        if waiting_type not in {"user", "waiting_user"} or correlation_id is None:
            raise HTTPException(
                status_code=409,
                detail="invalid_waiting_user_runtime_state",
            )
        inflight_resume_result = await db.execute(
            select(AgentRunCommand.id)
            .where(
                AgentRunCommand.tenant_id == tenant_id,
                AgentRunCommand.run_id == run.id,
                AgentRunCommand.command_type == "resume",
                AgentRunCommand.status.in_(("pending", "claimed")),
            )
            .limit(1)
        )
        resume_inflight = inflight_resume_result.scalar_one_or_none() is not None
        reconciliation_result = await db.execute(
            select(AgentToolExecution)
            .where(
                AgentToolExecution.tenant_id == tenant_id,
                AgentToolExecution.run_id == run.id,
                AgentToolExecution.status == "unknown",
            )
            .order_by(AgentToolExecution.started_at, AgentToolExecution.id)
        )
        pending_reconciliations = list(reconciliation_result.scalars().all())
    else:
        resume_inflight = False
        pending_reconciliations = []

    inflight_cancel_result = await db.execute(
        select(AgentRunCommand.id)
        .where(
            AgentRunCommand.tenant_id == tenant_id,
            AgentRunCommand.run_id == run.id,
            AgentRunCommand.command_type == "cancel",
            AgentRunCommand.status.in_(("pending", "claimed")),
        )
        .limit(1)
    )
    cancel_inflight = inflight_cancel_result.scalar_one_or_none() is not None

    terminal = view.execution_status in {"completed", "failed", "cancelled"}
    return SessionRuntimeStateOut(
        active_run=ActiveRunOut(
            run_id=str(view.run_id),
            thread_id=view.thread_id,
            session_id=str(view.session_id),
            status=view.execution_status,
            waiting_type=waiting_type,
            waiting_reason=view.waiting_reason,
            correlation_id=correlation_id,
            model_step_count=view.model_step_count,
            can_resume=(
                view.execution_status == "waiting_user"
                and not resume_inflight
                and not cancel_inflight
                and not pending_reconciliations
            ),
            can_cancel=not terminal and not cancel_inflight,
            pending_tool_reconciliations=[
                PendingToolReconciliationOut(
                    execution_id=str(execution.id),
                    tool_call_id=execution.tool_call_id,
                    tool_name=execution.tool_name,
                    result_summary=execution.result_summary,
                    error_code=(
                        execution.result_metadata.get("error_code")
                        if isinstance(execution.result_metadata, dict)
                        and isinstance(execution.result_metadata.get("error_code"), str)
                        else None
                    ),
                    can_reconcile=(
                        execution.tool_name == "write_file"
                        and execution.effect == "write"
                        and execution.retry_policy == "conditional"
                    ),
                )
                for execution in pending_reconciliations
            ],
        )
    )


@router.post(
    "/{agent_id}/sessions/{session_id}/runs/{run_id}/tool-executions/{execution_id}/reconcile",
    response_model=ReconcileToolExecutionOut,
)
async def reconcile_direct_tool_execution(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    body: ReconcileToolExecutionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReconcileToolExecutionOut:
    """Settle a Direct Chat unknown receipt before the user resumes its Run."""
    agent, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == current_user.id,
            ChatSession.session_type == "direct",
            ChatSession.group_id.is_(None),
            ChatSession.source_channel == "web",
            ChatSession.deleted_at.is_(None),
        )
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    run_result = await db.execute(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.tenant_id == tenant_id,
            AgentRun.agent_id == agent_id,
            AgentRun.session_id == session_id,
            AgentRun.origin_user_id == current_user.id,
            AgentRun.source_type == "chat",
            AgentRun.run_kind == "foreground",
            AgentRun.runtime_type == "langgraph",
            AgentRun.runtime_thread_id == str(session_id),
            AgentRun.lane_held.is_(True),
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Active Run not found")

    try:
        async with _open_run_state_reader(db) as reader:
            view = await reader.get_run_state(tenant_id, run_id)
    except RunStateReadError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    if view.execution_status != "waiting_user":
        raise HTTPException(status_code=409, detail="run_is_not_waiting_for_user")
    if (
        view.waiting_correlation_id is None
        or view.waiting_correlation_id != body.correlation_id.strip()
    ):
        raise HTTPException(
            status_code=409,
            detail="tool_reconciliation_correlation_mismatch",
        )

    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="reconciliation_note_required")
    try:
        execution = await reconcile_unknown_tool_execution(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            execution_id=execution_id,
            confirmed_status=(
                "succeeded" if body.outcome == "applied" else "failed"
            ),
            confirmed_by_user_id=current_user.id,
            note=note,
        )
    except ToolExecutionError as exc:
        status_code = 404 if exc.code == "tool_execution_not_found" else 409
        raise HTTPException(status_code=status_code, detail=exc.code) from exc

    db.add(
        AuditLog(
            user_id=current_user.id,
            agent_id=agent.id,
            action="runtime_tool_execution_reconciled",
            details={
                "tenant_id": str(tenant_id),
                "session_id": str(session_id),
                "run_id": str(run_id),
                "execution_id": str(execution_id),
                "tool_name": execution.tool_name,
                "confirmed_outcome": body.outcome,
                "status": execution.status,
                "note": note[:2_000],
            },
        )
    )
    await db.commit()
    return ReconcileToolExecutionOut(
        execution_id=str(execution.id),
        status=execution.status,  # type: ignore[arg-type]
        result_summary=execution.result_summary or "",
    )


@router.patch("/{agent_id}/sessions/{session_id}")
async def rename_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    body: PatchSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename one active direct session."""
    agent, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChatSession).where(
            *_active_direct_filters(tenant_id, agent_id),
            ChatSession.id == session_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _authorize_session_owner(current_user, agent, session)

    session.title = body.title
    session.updated_at = datetime.now(UTC)
    await db.commit()
    return {"id": str(session.id), "title": session.title}


@router.delete("/{agent_id}/sessions/{session_id}", status_code=204)
async def delete_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a direct session and cancel only its foreground collaboration."""
    agent, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChatSession).where(
            *_active_direct_filters(tenant_id, agent_id),
            ChatSession.id == session_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _authorize_session_owner(current_user, agent, session)
    if session.user_id is None:
        raise HTTPException(status_code=404, detail="Session not found")

    deleted = await soft_delete_direct_session(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=session.user_id,
        session_id=session_id,
        actor_user_id=current_user.id,
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.commit()
    return None


def _parse_message_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    timestamp_text, separator, message_id_text = cursor.rpartition("|")
    try:
        if separator:
            message_id = uuid.UUID(message_id_text)
        else:
            timestamp_text = cursor
            # Legacy timestamp-only cursors may duplicate equal-timestamp messages,
            # but never skip them. New clients should round-trip the emitted cursor.
            message_id = uuid.UUID(int=(1 << 128) - 1)
        created_at = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid `before` cursor. Use '<ISO 8601>|<message UUID>'.",
        ) from None
    return created_at, message_id


def _message_cursor(message: ChatMessage) -> str:
    return f"{message.created_at.isoformat()}|{message.id}"


def _base_message_entry(message: ChatMessage) -> dict:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "cursor": _message_cursor(message),
    }


@router.get("/{agent_id}/sessions/{session_id}/messages")
async def get_session_messages(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500, description="Messages to return")] = 20,
    before: Annotated[
        str | None,
        Query(description="Cursor '<created_at>|<id>' for the first excluded position"),
    ] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return associated session messages by authoritative `(created_at, id)` position."""
    agent, tenant_id = await _check_direct_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChatSession).where(
            *_active_agent_session_filters(tenant_id, agent_id),
            ChatSession.id == session_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _authorize_session_owner(current_user, agent, session)

    query = (
        select(ChatMessage)
        .join(ChatSession, ChatMessage.conversation_id == cast(ChatSession.id, String))
        .where(
            *_active_agent_session_filters(tenant_id, agent_id),
            ChatSession.id == session_id,
            ChatMessage.conversation_id == str(session_id),
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    if before:
        before_created_at, before_id = _parse_message_cursor(before)
        query = query.where(tuple_(ChatMessage.created_at, ChatMessage.id) < tuple_(before_created_at, before_id))
    message_result = await db.execute(query)
    messages = list(reversed(message_result.scalars().all()))

    if session.session_type == "direct" and str(session.user_id) == str(current_user.id):
        read_at = datetime.now(UTC)
        session.last_read_at_by_user = read_at
        session.updated_at = read_at
        await db.commit()

    sender_names: dict[str, str] = {}
    if _is_a2a_session(session):
        participant_ids = {message.participant_id for message in messages if message.participant_id}
        if participant_ids:
            participant_result = await db.execute(
                select(Participant.id, Participant.display_name)
                .join(
                    Agent,
                    and_(
                        Participant.type == "agent",
                        Participant.ref_id == Agent.id,
                    ),
                )
                .where(
                    Participant.id.in_(participant_ids),
                    Agent.tenant_id == tenant_id,
                )
            )
            sender_names = {str(row[0]): row[1] or "Unknown" for row in participant_result.all()}

    output = []
    for message in messages:
        sender_name = sender_names.get(str(message.participant_id)) if message.participant_id else None
        entry = _base_message_entry(message)
        if message.role == "tool_call":
            try:
                data = json.loads(message.content)
            except (TypeError, ValueError):
                data = None
            if isinstance(data, dict):
                entry["content"] = ""
                entry["toolName"] = data.get("name") or data.get("tool_name") or ""
                entry["toolArgs"] = data.get("args") or data.get("arguments")
                entry["toolStatus"] = data.get("status", "done")
                entry["toolResult"] = data.get("result", "")
                entry["toolThinking"] = data.get("reasoning_content", "")
                entry["toolCallId"] = data.get("tool_call_id") or ""
        if getattr(message, "thinking", None):
            entry["thinking"] = message.thinking
        if sender_name:
            entry["sender_name"] = sender_name
        if message.participant_id:
            entry["participant_id"] = str(message.participant_id)
        if _is_a2a_session(session) and message.role == "assistant" and "```tool_code" in (message.content or ""):
            for part in _split_inline_tools(message.content):
                part["id"] = str(message.id)
                part["created_at"] = message.created_at.isoformat() if message.created_at else None
                part["cursor"] = _message_cursor(message)
                if sender_name:
                    part["sender_name"] = sender_name
                if message.participant_id:
                    part["participant_id"] = str(message.participant_id)
                output.append(part)
        else:
            output.append(entry)
    return output


def _split_inline_tools(content: str) -> list[dict]:
    """Legacy parser retained for clients rendering archived inline tool blocks."""
    pattern = re.compile(
        r"```tool_code\s*\n\s*(\w+)\s*\n```"
        r"(?:\s*```json\s*\n(.*?)\n```)?",
        re.DOTALL,
    )
    parts: list[dict] = []
    last_end = 0
    for match in pattern.finditer(content):
        text_before = content[last_end : match.start()].strip()
        if text_before:
            parts.append({"role": "assistant", "content": text_before})
        args_str = match.group(2)
        tool_args = None
        if args_str:
            try:
                tool_args = json.loads(args_str.strip())
            except (TypeError, ValueError):
                tool_args = {"raw": args_str.strip()}
        parts.append(
            {
                "role": "tool_call",
                "content": "",
                "toolName": match.group(1),
                "toolArgs": tool_args,
                "toolStatus": "done",
                "toolResult": "",
            }
        )
        last_end = match.end()
    trailing = content[last_end:].strip()
    if trailing:
        parts.append({"role": "assistant", "content": trailing})
    return parts or [{"role": "assistant", "content": content}]
