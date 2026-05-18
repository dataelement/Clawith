"""Agent collaboration service — Agent-to-Agent communication."""

import json
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.audit import AuditLog
from app.services.storage import store_agent_bytes


class CollaborationService:
    """Enable digital employees to collaborate with each other.

    Collaboration patterns:
    1. Delegate — Agent A sends a task to Agent B
    2. Consult — Agent A asks Agent B a question and waits for response
    3. Notify — Agent A sends information to Agent B (fire-and-forget)
    """

    async def delegate_task(
        self, db: AsyncSession, from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID, task_title: str, task_description: str
    ) -> dict:
        """Agent A delegates a task to Agent B."""
        from app.models.task import Task

        # Verify both agents exist and are running
        from_result = await db.execute(select(Agent).where(Agent.id == from_agent_id))
        from_agent = from_result.scalar_one_or_none()
        to_result = await db.execute(select(Agent).where(Agent.id == to_agent_id))
        to_agent = to_result.scalar_one_or_none()

        if not from_agent or not to_agent:
            raise ValueError("Agent not found")
        if to_agent.status != "running":
            raise ValueError(f"Target agent '{to_agent.name}' is not running")

        # Create task for target agent
        task = Task(
            agent_id=to_agent_id,
            title=f"[委托自 {from_agent.name}] {task_title}",
            description=task_description,
            type="todo",
            priority="medium",
            created_by=from_agent.creator_id,
            assignee="self",
        )
        db.add(task)

        # Audit log
        db.add(AuditLog(
            agent_id=from_agent_id,
            action="collaboration:delegate",
            details={
                "from_agent": str(from_agent_id),
                "to_agent": str(to_agent_id),
                "task_title": task_title,
            },
        ))
        await db.flush()

        logger.info(f"Agent {from_agent.name} delegated task to {to_agent.name}: {task_title}")
        return {
            "task_id": str(task.id),
            "from_agent": from_agent.name,
            "to_agent": to_agent.name,
            "status": "delegated",
        }

    async def list_collaborators(self, db: AsyncSession, agent_id: uuid.UUID) -> list[dict]:
        """List agents that can collaborate with the given agent.

        Returns agents from the same enterprise (same creator's org).
        """
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return []

        # Find agents by same creator or with company-wide permissions
        collaborators_result = await db.execute(
            select(Agent).where(
                Agent.id != agent_id,
                Agent.status.in_(["running", "stopped"]),
            ).order_by(Agent.name)
        )
        agents = collaborators_result.scalars().all()

        return [
            {
                "id": str(a.id),
                "name": a.name,
                "role": a.role_description,
                "status": a.status,
            }
            for a in agents
        ]

    async def send_message_between_agents(
        self, db: AsyncSession, from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID, message: str, msg_type: str = "notify"
    ) -> dict:
        """Send an inter-agent message.

        msg_type: 'notify' (fire-and-forget) or 'consult' (expects reply)
        """
        from_result = await db.execute(select(Agent).where(Agent.id == from_agent_id))
        from_agent = from_result.scalar_one_or_none()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rel_path = f"workspace/inbox/{timestamp}_{str(from_agent_id)[:8]}.md"
        await store_agent_bytes(
            to_agent_id,
            rel_path,
            f"# 来自 {from_agent.name if from_agent else 'Unknown'} 的消息\n"
            f"- 类型: {msg_type}\n"
            f"- 时间: {datetime.now(timezone.utc).isoformat()}\n\n"
            f"{message}\n".encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        db.add(AuditLog(
            agent_id=from_agent_id,
            action=f"collaboration:{msg_type}",
            details={"to_agent": str(to_agent_id), "message_preview": message[:100]},
        ))
        await db.flush()

        return {"status": "sent", "type": msg_type}


collaboration_service = CollaborationService()
