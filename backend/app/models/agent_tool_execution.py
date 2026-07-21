"""Idempotency ledger for Agent tool executions."""

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


class AgentToolExecution(Base):
    """A durable reservation and result reference for a model tool call."""

    __tablename__ = "agent_tool_executions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_tool_executions"),
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'unknown')",
            name="ck_agent_tool_executions_status",
        ),
        CheckConstraint(
            "effect IN ('read', 'write', 'external_write')",
            name="ck_agent_tool_executions_effect",
        ),
        CheckConstraint(
            "retry_policy IN ('safe', 'conditional', 'never')",
            name="ck_agent_tool_executions_retry_policy",
        ),
        CheckConstraint(
            "attempt_count >= 1",
            name="ck_agent_tool_executions_attempt_count",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_tool_executions_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "tool_call_id", name="uq_agent_tool_executions_run_tool_call"),
        Index(
            "ix_agent_tool_executions_tenant_status_started",
            "tenant_id",
            "status",
            "started_at",
        ),
        Index("ix_agent_tool_executions_status_lease", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_agent_tool_executions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    assistant_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    sanitized_arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    effect: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="external_write",
        server_default="external_write",
    )
    retry_policy: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="never",
        server_default="never",
    )
    # Durable provider-attempt budget for this exact (run_id, tool_call_id)
    # receipt. It is independent from model turns and Command retries.
    attempt_count: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        server_default="1",
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
