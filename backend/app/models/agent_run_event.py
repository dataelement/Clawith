"""Stable product events projected from Agent runtime checkpoints."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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


class AgentRunEvent(Base):
    """An append-only, rebuildable product event for one Agent run."""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_run_events"),
        CheckConstraint(
            "event_type IN ('run_created', 'status_changed', 'waiting_started', 'resumed', "
            "'evidence_added', 'verification_updated', 'run_completed', 'run_failed', "
            "'run_cancelled', 'delivery_succeeded', 'delivery_failed', "
            "'channel_delivery_delivered', 'channel_delivery_failed')",
            name="ck_agent_run_events_event_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_run_events_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_events_run_idempotency"),
        UniqueConstraint(
            "run_id",
            "source_checkpoint_id",
            "event_type",
            name="uq_agent_run_events_checkpoint_type",
        ),
        Index("ix_agent_run_events_run_created", "run_id", "created_at"),
        Index(
            "ix_agent_run_events_tenant_type_created",
            "tenant_id",
            "event_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_agent_run_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_agent_run_events_agent_id_agents",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    artifact_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
