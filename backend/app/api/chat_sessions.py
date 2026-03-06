"""Chat session management API endpoints."""

import uuid
from datetime import datetime, timezone as tz
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.agent import Agent
from app.models.user import User

router = APIRouter(prefix="/api/agents", tags=["chat-sessions"])


def _is_admin_or_creator(user: User, agent: Agent) -> bool:
    return (
        user.role in ("platform_admin", "org_admin")
        or str(agent.creator_id) == str(user.id)
    )


class SessionOut(BaseModel):
    id: str
    agent_id: str
    user_id: str
    username: Optional[str] = None      # display_name ?? username
    source_channel: str = "web"         # web / feishu / discord / slack
    title: str
    created_at: str
    last_message_at: Optional[str] = None
    message_count: int = 0

    class Config:
        from_attributes = True


class CreateSessionIn(BaseModel):
    title: Optional[str] = None


class PatchSessionIn(BaseModel):
    title: str


@router.get("/{agent_id}/sessions")
async def list_sessions(
    agent_id: uuid.UUID,
    scope: str = Query("mine", description="'mine' or 'all'"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions for an agent. 'all' requires admin or creator role."""
    # Verify agent exists
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if scope == "all":
        if not _is_admin_or_creator(current_user, agent):
            raise HTTPException(status_code=403, detail="Not authorized to view all sessions")

        # Fetch all sessions with display names
        from sqlalchemy import coalesce
        result = await db.execute(
            select(ChatSession, coalesce(User.display_name, User.username).label("display"))
            .join(User, ChatSession.user_id == User.id)
            .where(ChatSession.agent_id == agent_id)
            .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
        )
        rows = result.all()
        out = []
        for session, display in rows:
            count_result = await db.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == str(session.id),
                    ChatMessage.agent_id == agent_id,
                )
            )
            count = count_result.scalar() or 0
            if count == 0:
                continue  # hide empty sessions
            out.append(SessionOut(
                id=str(session.id),
                agent_id=str(session.agent_id),
                user_id=str(session.user_id),
                username=display,
                source_channel=session.source_channel,
                title=session.title,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                message_count=count,
            ))
        return out

    else:  # scope == "mine"
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.agent_id == agent_id, ChatSession.user_id == current_user.id)
            .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
        )
        sessions = result.scalars().all()
        out = []
        for session in sessions:
            # Count only — skip sessions with no user messages (orphan assistant-only records)
            count_result = await db.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == str(session.id),
                    ChatMessage.agent_id == agent_id,
                    ChatMessage.role == "user",
                )
            )
            user_msg_count = count_result.scalar() or 0
            if user_msg_count == 0:
                continue  # hide empty or orphan sessions
            # Total message count for display
            total_result = await db.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == str(session.id),
                    ChatMessage.agent_id == agent_id,
                )
            )
            count = total_result.scalar() or 0
            out.append(SessionOut(
                id=str(session.id),
                agent_id=str(session.agent_id),
                user_id=str(session.user_id),
                source_channel=session.source_channel,
                title=session.title,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                message_count=count,
            ))
        return out


@router.post("/{agent_id}/sessions", status_code=201)
async def create_session(
    agent_id: uuid.UUID,
    body: CreateSessionIn = CreateSessionIn(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session for the current user."""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(tz.utc)
    session = ChatSession(
        agent_id=agent_id,
        user_id=current_user.id,
        title=body.title or f"Session {now.strftime('%m-%d %H:%M')}",
        created_at=now,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut(
        id=str(session.id),
        agent_id=str(session.agent_id),
        user_id=str(session.user_id),
        title=session.title,
        created_at=session.created_at.isoformat(),
        last_message_at=None,
        message_count=0,
    )


@router.patch("/{agent_id}/sessions/{session_id}")
async def rename_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    body: PatchSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a session. Only owner, admin, or creator can rename."""
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.agent_id == agent_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()

    if str(session.user_id) != str(current_user.id) and not _is_admin_or_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized")

    session.title = body.title
    await db.commit()
    return {"id": str(session.id), "title": session.title}


@router.get("/{agent_id}/sessions/{session_id}/messages")
async def get_session_messages(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get chat messages for a specific session."""
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.agent_id == agent_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Permission: owner, admin, or creator can view
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if str(session.user_id) != str(current_user.id) and not _is_admin_or_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized to view this session")

    msgs_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == str(session_id), ChatMessage.agent_id == agent_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(500)
    )
    messages = msgs_result.scalars().all()

    out = []
    for m in messages:
        entry: dict = {"role": m.role, "content": m.content}
        if m.role == "tool_call":
            try:
                import json
                data = json.loads(m.content)
                entry["content"] = ""
                entry["toolName"] = data.get("name", "")
                entry["toolArgs"] = data.get("args")
                entry["toolStatus"] = data.get("status", "done")
                entry["toolResult"] = data.get("result", "")
            except Exception:
                pass
        out.append(entry)
    return out
