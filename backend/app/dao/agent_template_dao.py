"""DAO for agent templates."""

from typing import Any, Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.agent import AgentTemplate


class AgentTemplateDAO(BaseDAO[AgentTemplate]):
    """Reusable accessors for the template marketplace."""

    def __init__(self) -> None:
        super().__init__(AgentTemplate)

    async def list_templates(self, *, category: str | None = None) -> Sequence[AgentTemplate]:
        """List templates ordered for display."""
        async with self.session(readonly=True) as db:
            query = select(AgentTemplate).order_by(AgentTemplate.name)
            if category:
                query = query.where(AgentTemplate.category == category)
            result = await db.execute(query)
            return result.scalars().all()

    async def create_template(self, *, obj_in: dict[str, Any]) -> AgentTemplate:
        """Create a template and flush it for immediate serialization."""
        return await self.create(obj_in=obj_in)


agent_template_dao = AgentTemplateDAO()
