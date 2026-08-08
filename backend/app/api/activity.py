"""Activity log API — view agent work history."""

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.core.permissions import check_agent_access
from app.dao import activity_dao
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["activity"])


@router.get("/agents/{agent_id}/activity")
async def get_agent_activity(
    agent_id: uuid.UUID,
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get recent activity logs for an agent."""
    await check_agent_access(db, current_user, agent_id)

    logs = await activity_dao.list_agent_activity(agent_id=agent_id, limit=limit)

    return [
        {
            "id": str(log.id),
            "action_type": log.action_type,
            "summary": log.summary,
            "detail": log.detail_json,
            "related_id": str(log.related_id) if log.related_id else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


# ─── Chat History (per-agent) ─────────────────────────────────

@router.get("/agents/{agent_id}/chat-history/conversations")
async def list_conversations(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all conversation partners for this agent (web users + other agents)."""
    await check_agent_access(db, current_user, agent_id)

    return await activity_dao.list_conversation_summaries(agent_id=agent_id)


@router.get("/agents/{agent_id}/chat-history/{conv_id:path}")
async def get_conversation_messages(
    agent_id: uuid.UUID,
    conv_id: str,
    limit: int = Query(100, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get messages for a specific conversation."""
    await check_agent_access(db, current_user, agent_id)

    return await activity_dao.list_conversation_messages(agent_id=agent_id, conv_id=conv_id, limit=limit)
