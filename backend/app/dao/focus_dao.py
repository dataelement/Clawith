"""DAO for structured agent focus items."""

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.dao.base import BaseDAO
from app.models.focus import AgentFocusItem


class FocusDAO(BaseDAO[AgentFocusItem]):
    """Persistence operations for agent focus state."""

    def __init__(self) -> None:
        super().__init__(AgentFocusItem)

    async def count_by_agent(self, agent_id: Any) -> int:
        """Count focus items for an agent."""
        async with self.session(readonly=True) as db:
            result = await db.scalar(select(func.count()).select_from(AgentFocusItem).where(AgentFocusItem.agent_id == agent_id))
            return int(result or 0)

    async def bulk_insert_legacy_rows(self, rows: list[dict[str, Any]]) -> int:
        """Insert migrated legacy rows, ignoring existing agent/key pairs."""
        if not rows:
            return 0
        async with self.session() as db:
            stmt = insert(AgentFocusItem).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["agent_id", "key"])
            result = await db.execute(stmt)
            await db.flush()
            return result.rowcount or 0

    async def list_by_agent(self, *, agent_id: Any, include_completed: bool) -> Sequence[AgentFocusItem]:
        """List focus items in display order."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentFocusItem).where(AgentFocusItem.agent_id == agent_id)
            if not include_completed:
                stmt = stmt.where(AgentFocusItem.status != "completed")
            stmt = stmt.order_by(
                AgentFocusItem.status.desc(),
                AgentFocusItem.kind.desc(),
                AgentFocusItem.sort_order.asc(),
                AgentFocusItem.created_at.asc(),
            )
            result = await db.execute(stmt)
            return result.scalars().all()

    async def upsert_item(
        self,
        *,
        agent_id: Any,
        key: str,
        title: str | None,
        description: str,
        status: str,
        kind: str,
        source: str,
        metadata: dict | None,
        completed_at: datetime | None,
    ) -> AgentFocusItem:
        """Create or update a focus item by agent/key."""
        async with self.session() as db:
            result = await db.execute(
                select(AgentFocusItem).where(
                    AgentFocusItem.agent_id == agent_id,
                    AgentFocusItem.key == key,
                )
            )
            item = result.scalar_one_or_none()
            if item:
                if title is not None:
                    item.title = title
                item.description = description or item.description or key
                item.status = status
                item.kind = kind
                item.source = source or item.source or "user"
                if metadata:
                    item.item_metadata = {**(item.item_metadata or {}), **metadata}
                item.completed_at = completed_at
            else:
                max_order = await db.scalar(
                    select(func.max(AgentFocusItem.sort_order)).where(AgentFocusItem.agent_id == agent_id)
                )
                item = AgentFocusItem(
                    agent_id=agent_id,
                    key=key,
                    title=title,
                    description=description or key,
                    status=status,
                    kind=kind,
                    source=source or "user",
                    item_metadata=metadata or {},
                    sort_order=(max_order or 0) + 1,
                    completed_at=completed_at,
                )
                db.add(item)
            await db.flush()
            await db.refresh(item)
            return item

    async def complete_item(self, *, agent_id: Any, key: str, completed_at: datetime) -> AgentFocusItem | None:
        """Mark a focus item completed."""
        async with self.session() as db:
            result = await db.execute(
                select(AgentFocusItem).where(
                    AgentFocusItem.agent_id == agent_id,
                    AgentFocusItem.key == key,
                )
            )
            item = result.scalar_one_or_none()
            if not item:
                return None
            item.status = "completed"
            item.completed_at = completed_at
            await db.flush()
            await db.refresh(item)
            return item


focus_dao = FocusDAO()
