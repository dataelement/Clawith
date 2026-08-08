"""Reliable input commands for the durable Agent runtime."""

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentRunCommand(Base):
    """A start, resume, or cancel input awaiting durable application to a Graph."""

    __tablename__ = "agent_run_commands"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_run_commands"),
        CheckConstraint(
            "command_type IN ('start', 'resume', 'cancel')",
            name="ck_agent_run_commands_command_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'applied', 'rejected')",
            name="ck_agent_run_commands_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_agent_run_commands_attempt_count"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_run_commands_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_commands_run_idempotency"),
        Index(
            "ix_agent_run_commands_status_claim_created",
            "status",
            "claim_expires_at",
            "created_at",
        ),
        Index("ix_agent_run_commands_run_created", "run_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_agent_run_commands_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    command_type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_agent_run_commands_actor_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_agent_run_commands_actor_agent_id_agents",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default=text("'pending'")
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    applied_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
