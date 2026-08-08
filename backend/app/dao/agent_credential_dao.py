"""DAO for agent credentials."""

from typing import Any, Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.agent_credential import AgentCredential


class AgentCredentialDAO(BaseDAO[AgentCredential]):
    """Credential persistence helpers scoped by agent."""

    def __init__(self) -> None:
        super().__init__(AgentCredential)

    async def list_by_agent(self, agent_id: Any) -> Sequence[AgentCredential]:
        """List credentials for an agent, newest first."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(AgentCredential)
                .where(AgentCredential.agent_id == agent_id)
                .order_by(AgentCredential.created_at.desc())
            )
            return result.scalars().all()

    async def get_by_agent(self, *, credential_id: Any, agent_id: Any) -> AgentCredential | None:
        """Fetch one credential by id and owning agent."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(AgentCredential).where(
                    AgentCredential.id == credential_id,
                    AgentCredential.agent_id == agent_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_for_agent(self, *, agent_id: Any, obj_in: dict[str, Any]) -> AgentCredential:
        """Create a credential for an agent."""
        async with self.session() as db:
            cred = AgentCredential(agent_id=agent_id, **obj_in)
            db.add(cred)
            await db.flush()
            await db.refresh(cred)
            return cred

    async def save(self, cred: AgentCredential) -> AgentCredential:
        """Persist an already-loaded credential."""
        async with self.session() as db:
            db.add(cred)
            await db.flush()
            await db.refresh(cred)
            return cred

    async def delete_by_agent(self, *, credential_id: Any, agent_id: Any) -> bool:
        """Delete a credential by id and owning agent."""
        async with self.session() as db:
            result = await db.execute(
                select(AgentCredential).where(
                    AgentCredential.id == credential_id,
                    AgentCredential.agent_id == agent_id,
                )
            )
            cred = result.scalar_one_or_none()
            if not cred:
                return False
            await db.delete(cred)
            await db.flush()
            return True


agent_credential_dao = AgentCredentialDAO()
