"""Structured focus items for agent working state."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentFocusItem(Base):
    """A structured focus item tracked by an agent.

    Focus is intentionally database-backed. It replaces the legacy focus.md
    working-state file so triggers, Aware, and agent tools share one source of
    truth with validation and stable identifiers.
    """

    __tablename__ = "agent_focus_items"
    __table_args__ = (
        UniqueConstraint("agent_id", "key", name="uq_agent_focus_items_agent_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="in_progress", index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="normal", index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="user", index=True)
    item_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
