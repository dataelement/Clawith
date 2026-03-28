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
    source_channel: str = "web"         # web / feishu / discord / slack / agent
    title: str
    created_at: str
    last_message_at: Optional[str] = None
    message_count: int = 0
    # Agent-to-agent session fields
    peer_agent_id: Optional[str] = None
    peer_agent_name: Optional[str] = None
    participant_type: str = "user"       # 'user' | 'agent'
    # Group chat session fields
    is_group: bool = False
    group_name: Optional[str] = None

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

        # Fetch all sessions (including agent-to-agent where this agent is peer)
        result = await db.execute(
            select(ChatSession)
            .where(
                (ChatSession.agent_id == agent_id)
                | ((ChatSession.peer_agent_id == agent_id) & (ChatSession.source_channel == "agent"))
            )
            .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
        )
        sessions = result.scalars().all()
        out = []
        for session in sessions:
            count_result = await db.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == str(session.id),
                )
            )
            count = count_result.scalar() or 0
            if count == 0:
                continue  # hide empty sessions

            # Determine display name based on session type
            display = None
            peer_agent_id = None
            peer_agent_name = None
            participant_type = "user"

            if session.source_channel == "agent" and session.peer_agent_id:
                # Agent-to-agent session
                participant_type = "agent"
                peer_agent_id = str(session.peer_agent_id)
                # Get both agent names
                a1_r = await db.execute(select(Agent.name).where(Agent.id == session.agent_id))
                a2_r = await db.execute(select(Agent.name).where(Agent.id == session.peer_agent_id))
                a1_name = a1_r.scalar_one_or_none() or "Agent"
                a2_name = a2_r.scalar_one_or_none() or "Agent"
                peer_agent_name = a2_name
                display = f"Agent {a1_name} - {a2_name}"
            elif session.is_group:
                # Group chat session — display group name instead of username
                display = session.group_name or session.title or "Group Chat"
            else:
                # Human session — resolve username
                user_r = await db.execute(
                    select(func.coalesce(User.display_name, User.username))
                    .where(User.id == session.user_id)
                )
                display = user_r.scalar_one_or_none() or "Unknown"

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
                peer_agent_id=peer_agent_id,
                peer_agent_name=peer_agent_name,
                participant_type="group" if session.is_group else participant_type,
                is_group=session.is_group,
                group_name=session.group_name,
            ))
        return out

    else:  # scope == "mine"
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.agent_id == agent_id,
                ChatSession.user_id == current_user.id,
                ChatSession.is_group == False,  # Group sessions are not "mine"
                ChatSession.source_channel.notin_(["agent", "trigger"]),  # Exclude agent-to-agent and reflection sessions
            )
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
    new_id = uuid.uuid4()
    session = ChatSession(
        id=new_id,
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


@router.delete("/{agent_id}/sessions/{session_id}", status_code=204)
async def delete_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and its messages. Owner, admin, or creator only."""
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

    # Delete associated messages first
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(ChatMessage).where(ChatMessage.conversation_id == str(session_id)))
    await db.delete(session)
    await db.commit()
    return None


@router.get("/{agent_id}/sessions/{session_id}/messages")
async def get_session_messages(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get chat messages for a specific session."""
    # Allow looking up sessions where agent_id OR peer_agent_id matches
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            (ChatSession.agent_id == agent_id) | (ChatSession.peer_agent_id == agent_id),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Permission: owner, admin, or creator can view
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if str(session.user_id) != str(current_user.id) and not _is_admin_or_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized to view this session")

    # Query messages by conversation_id only (agent-to-agent uses session_agent_id)
    msgs_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == str(session_id))
        .order_by(ChatMessage.created_at.asc())
        .limit(500)
    )
    messages = msgs_result.scalars().all()

    # Resolve sender names for agent sessions
    sender_cache: dict = {}
    if session.source_channel == "agent":
        from app.models.participant import Participant
        for m in messages:
            if m.participant_id and str(m.participant_id) not in sender_cache:
                p_r = await db.execute(select(Participant.display_name).where(Participant.id == m.participant_id))
                sender_cache[str(m.participant_id)] = p_r.scalar_one_or_none() or "Unknown"

    out = []
    for m in messages:
        sender_name = sender_cache.get(str(m.participant_id)) if m.participant_id else None

        if m.role == "tool_call":
            import json
            entry: dict = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            try:
                data = json.loads(m.content)
                entry["content"] = ""
                entry["toolName"] = data.get("name", "")
                entry["toolArgs"] = data.get("args")
                entry["toolStatus"] = data.get("status", "done")
                entry["toolResult"] = data.get("result", "")
            except Exception:
                pass
            if sender_name:
                entry["sender_name"] = sender_name
            out.append(entry)
            continue

        # For agent sessions, parse inline tool_code blocks from assistant messages
        if session.source_channel == "agent" and m.role == "assistant" and "```tool_code" in (m.content or ""):
            parts = _split_inline_tools(m.content)
            for part in parts:
                if sender_name:
                    part["sender_name"] = sender_name
                if m.participant_id:
                    part["participant_id"] = str(m.participant_id)
                out.append(part)
        else:
            entry = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            if hasattr(m, 'thinking') and m.thinking:
                entry["thinking"] = m.thinking
            if sender_name:
                entry["sender_name"] = sender_name
            if m.participant_id:
                entry["participant_id"] = str(m.participant_id)
            out.append(entry)

    return out


import re

def _split_inline_tools(content: str) -> list[dict]:
    """Parse assistant content containing inline ```tool_code blocks.

    Splits into alternating text segments and tool_call entries.
    Format: ```tool_code\ntool_name\n``` ```json\n{args}\n```
    """
    # Pattern: ```tool_code\n<name>\n``` optionally followed by ```json\n<args>\n```
    pattern = re.compile(
        r'```tool_code\s*\n\s*(\w+)\s*\n```'        # tool name
        r'(?:\s*```json\s*\n(.*?)\n```)?',            # optional JSON args
        re.DOTALL
    )

    parts: list[dict] = []
    last_end = 0

    for match in pattern.finditer(content):
        # Text before this tool call
        text_before = content[last_end:match.start()].strip()
        if text_before:
            parts.append({"role": "assistant", "content": text_before})

        tool_name = match.group(1)
        args_str = match.group(2)
        tool_args = None
        if args_str:
            try:
                import json
                tool_args = json.loads(args_str.strip())
            except Exception:
                tool_args = {"raw": args_str.strip()}

        parts.append({
            "role": "tool_call",
            "content": "",
            "toolName": tool_name,
            "toolArgs": tool_args,
            "toolStatus": "done",
            "toolResult": "",
        })
        last_end = match.end()

    # Trailing text after last tool
    trailing = content[last_end:].strip()
    if trailing:
        parts.append({"role": "assistant", "content": trailing})

    # If no matches found, return the whole content as-is
    if not parts:
        parts.append({"role": "assistant", "content": content})

    return parts
