"""Read access for AgentTrigger records used by public trigger endpoints."""

from typing import Any

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.models.trigger import AgentTrigger


class TriggerDAO(BaseDAO[AgentTrigger]):
    """DAO for trigger lookups that do not have a request tenant context."""

    def __init__(self) -> None:
        super().__init__(AgentTrigger)

    async def get_enabled_webhook_target(
        self, token: str, db: Any = None
    ) -> tuple[AgentTrigger, Agent] | None:
        """Return a token-matched webhook and its active agent in an active tenant."""
        async with self.session(db=db, readonly=True) as session_db:
            stmt = (
                select(AgentTrigger, Agent)
                .join(Agent, Agent.id == AgentTrigger.agent_id)
                .join(Tenant, Tenant.id == Agent.tenant_id)
                .where(
                    AgentTrigger.type == "webhook",
                    AgentTrigger.is_enabled.is_(True),
                    AgentTrigger.config["token"].astext == token,
                    Agent.deleted_at.is_(None),
                    Tenant.is_active.is_(True),
                )
                .limit(1)
            )
            return (await session_db.execute(stmt)).one_or_none()


trigger_dao = TriggerDAO()
