"""Durable outbox rows for sending Runtime messages to external channels."""

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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChannelDelivery(Base):
    """One retryable provider delivery for an already persisted ChatMessage.

    This table is an outbox only.  It never participates in Graph routing,
    checkpoint recovery, or Runtime lifecycle decisions.
    """

    __tablename__ = "channel_deliveries"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_channel_deliveries"),
        CheckConstraint(
            "channel IN ('feishu', 'dingtalk', 'wecom', 'wechat', 'whatsapp', "
            "'slack', 'discord', 'microsoft_teams')",
            name="ck_channel_deliveries_channel",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'delivered', 'failed')",
            name="ck_channel_deliveries_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_channel_deliveries_attempt_count",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_channel_deliveries_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_channel_deliveries_run_idempotency",
        ),
        UniqueConstraint(
            "message_id",
            name="uq_channel_deliveries_message_id",
        ),
        Index(
            "ix_channel_deliveries_pending_due",
            "status",
            "next_attempt_at",
            "claim_expires_at",
            "created_at",
        ),
        Index(
            "ix_channel_deliveries_run_created",
            "run_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_channel_deliveries_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_channel_deliveries_agent_id_agents",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "chat_sessions.id",
            name="fk_channel_deliveries_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "chat_messages.id",
            name="fk_channel_deliveries_message_id_chat_messages",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
