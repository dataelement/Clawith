"""Unified chat session model for direct, group, A2A, and trigger chats."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatSession(Base):
    """A named session on the unified chat substrate.

    source_channel: 'web' | 'feishu' | 'discord' | 'slack'
    external_conv_id: original channel conversation ID (e.g. 'feishu_p2p_ou_xxx').
                      Unique per agent — used for reliable find-or-create without in-process caching.
    is_group: True for group chat sessions (Feishu group, WeCom group, Slack channel, etc.).
              Group sessions have user_id=NULL and only appear in the 'all sessions' view.
    group_name: Display name for group chat sessions (e.g. the group/channel name from IM platform).
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("agent_id", "external_conv_id", name="uq_chat_sessions_agent_ext_conv"),
        UniqueConstraint("tenant_id", "id", name="uq_chat_sessions_tenant_id_id"),
        CheckConstraint(
            "session_type IN ('direct', 'group', 'a2a', 'trigger')",
            name="ck_chat_sessions_session_type",
        ),
        Index(
            "uq_chat_sessions_primary_direct",
            "tenant_id",
            "agent_id",
            "user_id",
            unique=True,
            postgresql_where=text(
                "session_type = 'direct' AND is_primary = true AND deleted_at IS NULL"
            ),
        ),
        Index(
            "uq_chat_sessions_primary_group",
            "group_id",
            unique=True,
            postgresql_where=text(
                "session_type = 'group' AND group_id IS NOT NULL "
                "AND is_primary = true AND deleted_at IS NULL"
            ),
        ),
        Index("ix_chat_sessions_tenant_id", "tenant_id"),
        Index("ix_chat_sessions_group_id", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_chat_sessions_tenant_id_tenants"),
        nullable=False,
    )
    session_type: Mapped[str] = mapped_column(String(20), nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_chat_sessions_group_id_groups"),
        nullable=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True
    )
    # user_id: for P2P sessions this is the user; for group sessions this is the agent creator (placeholder)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    created_by_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_chat_sessions_created_by_participant_id_participants",
        ),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New Session")
    source_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="web")
    external_conv_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Group chat support: group sessions have user_id=NULL and show group_name instead
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    group_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Participant identity (unified User/Agent identity)
    participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id"), nullable=True)
    # For agent-to-agent sessions: the other agent in the conversation
    peer_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    # Primary platform session: the long-lived first-party conversation that agent-initiated
    # messages should land in. User-created side-topic sessions remain temporary (`is_primary=false`).
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False, index=True)
    # Tracks when the owning platform user last opened/read this session. Unread badges are derived
    # from non-user messages created after this timestamp.
    last_read_at_by_user: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
