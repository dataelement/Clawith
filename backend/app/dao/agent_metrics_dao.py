"""DAO for agent metrics."""

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select

from app.dao.base import BaseDAO
from app.models.audit import ApprovalRequest, AuditLog
from app.models.task import Task


class AgentMetricsDAO(BaseDAO[Task]):
    """Aggregated metrics queries for agent observability."""

    def __init__(self) -> None:
        super().__init__(Task)

    async def get_agent_metrics_counts(self, *, agent_id: Any, recent_cutoff: datetime) -> dict[str, int]:
        """Return task, approval, and recent audit counts in three compact queries."""
        async with self.session(readonly=True) as db:
            task_result = await db.execute(
                select(
                    func.count(Task.id),
                    func.coalesce(func.sum(case((Task.status == "done", 1), else_=0)), 0),
                    func.coalesce(func.sum(case((Task.status == "pending", 1), else_=0)), 0),
                ).where(Task.agent_id == agent_id)
            )
            total_tasks, done_tasks, pending_tasks = task_result.one()

            approval_result = await db.execute(
                select(
                    func.count(ApprovalRequest.id),
                    func.coalesce(func.sum(case((ApprovalRequest.status == "pending", 1), else_=0)), 0),
                ).where(ApprovalRequest.agent_id == agent_id)
            )
            total_approvals, pending_approvals = approval_result.one()

            recent_result = await db.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.agent_id == agent_id,
                    AuditLog.created_at >= recent_cutoff,
                )
            )

            return {
                "total_tasks": int(total_tasks or 0),
                "done_tasks": int(done_tasks or 0),
                "pending_tasks": int(pending_tasks or 0),
                "total_approvals": int(total_approvals or 0),
                "pending_approvals": int(pending_approvals or 0),
                "recent_actions": int(recent_result.scalar() or 0),
            }


agent_metrics_dao = AgentMetricsDAO()
