"""Activity logger — simple async function to record agent actions."""

import uuid

from loguru import logger

from app.dao import query_dao
from app.models.activity_log import AgentActivityLog


async def log_activity(
    agent_id: uuid.UUID,
    action_type: str,
    summary: str,
    detail: dict | None = None,
    related_id: uuid.UUID | None = None,
) -> None:
    """Record an agent activity. Fire-and-forget, never raises."""
    try:
        async with query_dao.session() as db:
            query_dao.add(db, AgentActivityLog(
                agent_id=agent_id,
                action_type=action_type,
                summary=summary,
                detail_json=detail,
                related_id=related_id,
            ))
            await query_dao.commit(db)
    except Exception as e:
        logger.error(f"[ActivityLog] Failed to log {action_type}: {e}")
