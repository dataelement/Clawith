"""Latest compacted context state for a chat session."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionContextState(Base):
    """The current rolling Session Context and its optimistic-lock version."""

    __tablename__ = "session_context_states"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_session_context_states"),
        CheckConstraint("version >= 1", name="ck_session_context_states_version"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["chat_sessions.tenant_id", "chat_sessions.id"],
            name="fk_session_context_states_tenant_session_chat_sessions",
            ondelete="CASCADE",
        ),
        UniqueConstraint("session_id", name="uq_session_context_states_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_session_context_states_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_session_context_states_agent_id_agents",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    requirements: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    decisions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    open_items: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    workspace_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    covered_through_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "chat_messages.id",
            name="fk_session_context_states_covered_message_id_chat_messages",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


Index(
    "ix_session_context_states_tenant_agent_updated",
    SessionContextState.tenant_id,
    SessionContextState.agent_id,
    SessionContextState.updated_at.desc(),
)
