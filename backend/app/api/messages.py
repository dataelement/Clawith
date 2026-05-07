"""Messages API — inbox, unread count, mark as read.

After the Participant abstraction migration, agent-to-agent messages are stored
in chat_messages (via ChatSession with source_channel='agent').
This API now queries chat_sessions + chat_messages for the inbox.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.participant import Participant
from app.models.user import User

router = APIRouter(tags=["messages"])


@router.get("/messages/inbox")
async def get_inbox(
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent-to-agent messages for agents the current user manages.

    Returns recent messages from ChatSessions with source_channel='agent'
    where the user's agents are participants.
    """
    # Find agents the current user created
    agent_ids_q = await db.execute(select(Agent.id).where(Agent.creator_id == current_user.id))
    my_agent_ids = [r[0] for r in agent_ids_q.fetchall()]

    if not my_agent_ids:
        return []

    # Find agent-to-agent chat sessions involving the user's agents
    sessions_q = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.source_channel == "agent",
            (ChatSession.agent_id.in_(my_agent_ids)) | (ChatSession.peer_agent_id.in_(my_agent_ids)),
        )
        .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
        .limit(limit)
    )
    sessions = sessions_q.scalars().all()

    result_list = []
    for sess in sessions:
        # Get latest messages from this session
        msgs_q = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == str(sess.id))
            .order_by(ChatMessage.created_at.desc())
            .limit(3)
        )
        for msg in msgs_q.scalars().all():
            sender_name = "未知"
            if msg.participant_id:
                p_r = await db.execute(select(Participant.display_name).where(Participant.id == msg.participant_id))
                sender_name = p_r.scalar_one_or_none() or "未知"

            result_list.append({
                "id": str(msg.id),
                "sender_type": "agent",
                "sender_name": sender_name,
                "content": msg.content,
                "session_title": sess.title,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            })

    # Sort by created_at desc and limit
    result_list.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return result_list[:limit]


@router.get("/messages/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get count of unread agent-to-agent messages for the current user's agents."""
    agent_ids_q = await db.execute(select(Agent.id).where(Agent.creator_id == current_user.id))
    my_agent_ids = [r[0] for r in agent_ids_q.fetchall()]

    if not my_agent_ids:
        return {"unread_count": 0}

    # Count agent-to-agent sessions with recent activity
    # (Since we don't have per-message read tracking on ChatMessage yet,
    # just return 0 for now — this can be enhanced later)
    return {"unread_count": 0}
