"""Chat session model for grouping web chat messages per user-agent pair."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, ForeignKey, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatSession(Base):
    """A named session grouping chat messages between a user and an agent.

    source_channel: 'web' | 'feishu' | 'discord' | 'slack'
    external_conv_id: original channel conversation ID (e.g. 'feishu_p2p_ou_xxx').
                      Unique per agent — used for reliable find-or-create without in-process caching.
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("agent_id", "external_conv_id", name="uq_chat_sessions_agent_ext_conv"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New Session")
    source_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="web")
    external_conv_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
