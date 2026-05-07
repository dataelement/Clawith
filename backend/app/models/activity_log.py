"""Activity log model for tracking agent actions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func, UniqueConstraint, Integer
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentActivityLog(Base):
    """Records every action taken by a digital employee."""

    __tablename__ = "agent_activity_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(
        Enum(
            "chat_reply", "tool_call", "feishu_msg_sent", "agent_msg_sent",
            "web_msg_sent", "task_created", "task_updated", "file_written", "error",
            "schedule_run", "heartbeat", "plaza_post",
            name="activity_action_enum",
            create_constraint=False,
        ),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    detail_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    related_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class DailyTokenUsage(Base):
    """Rolled up token consumption per agent per day for time-series analytics."""

    __tablename__ = "daily_token_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Add a unique constraint to allow ON CONFLICT UPSERT for efficient daily token aggregation
    __table_args__ = (
        UniqueConstraint("agent_id", "date", name="uq_daily_token_usage_agent_date"),
    )
