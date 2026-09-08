"""Private Run schema records; lifecycle behavior is implemented in a later gate."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class RunRecord(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "agent_id", "id", name="uq_agent_runs_tenant_agent_id"),
        UniqueConstraint(
            "tenant_id", "initiator_kind", "initiator_owner_id", "source_key", name="uq_agent_runs_source_identity"
        ),
        CheckConstraint(
            "status IN ('Running', 'Waiting', 'Completed', 'Failed', 'Cancelled', 'Interrupted')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("latest_history_sequence >= 0", name="ck_agent_runs_latest_history_sequence"),
        CheckConstraint("parent_run_id IS NULL OR parent_run_id <> id", name="ck_agent_runs_not_own_parent"),
        Index("ix_agent_runs_active_parent", "tenant_id", "parent_run_id", "id",
              postgresql_where=text("status IN ('Running', 'Waiting')")),
        Index("ix_agent_runs_active_roots", "id",
              postgresql_where=text("parent_run_id IS NULL AND status IN ('Running', 'Waiting')")),
        CheckConstraint(
            "(status = 'Waiting' AND active_waiting_reference IS NOT NULL) OR "
            "(status <> 'Waiting' AND active_waiting_reference IS NULL)",
            name="ck_agent_runs_waiting_reference",
        ),
        CheckConstraint(
            "(status IN ('Completed', 'Failed', 'Cancelled', 'Interrupted') AND finished_at IS NOT NULL) OR "
            "(status IN ('Running', 'Waiting') AND finished_at IS NULL)",
            name="ck_agent_runs_finished_at",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "parent_run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "run"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    parent_run_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    initiator_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    initiator_owner_id: Mapped[UUID] = mapped_column(nullable=False)
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    latest_history_sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    active_waiting_reference: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunSnapshotRecord(Base):
    __tablename__ = "agent_run_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", name="uq_agent_run_snapshots_tenant_run"),
        CheckConstraint("schema_version > 0", name="ck_agent_run_snapshots_schema_version"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_agent_run_snapshots_content_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["agent_runs.tenant_id", "agent_runs.id"], ondelete="RESTRICT"
        ),
        {"info": {"owner": "run"}},
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunHistoryRecord(Base):
    __tablename__ = "agent_run_history"
    __table_args__ = (
        Index("ix_agent_run_history_kind_sequence", "tenant_id", "run_id", "payload_kind", "sequence"),
        UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_agent_run_history_tenant_sequence"),
        CheckConstraint("sequence > 0", name="ck_agent_run_history_sequence"),
        CheckConstraint("payload_schema_version > 0", name="ck_agent_run_history_payload_version"),
        CheckConstraint(
            "(source_kind IS NULL AND source_owner_id IS NULL AND source_key IS NULL) OR "
            "(source_kind IS NOT NULL AND source_owner_id IS NOT NULL AND source_key IS NOT NULL)",
            name="ck_agent_run_history_source_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["agent_runs.tenant_id", "agent_runs.id"], ondelete="RESTRICT"
        ),
        Index(
            "uq_agent_run_history_source",
            "run_id",
            "source_kind",
            "source_owner_id",
            "source_key",
            unique=True,
            postgresql_where=text("source_kind IS NOT NULL"),
        ),
        {"info": {"owner": "run"}},
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_kind: Mapped[str | None] = mapped_column(String(64))
    source_owner_id: Mapped[UUID | None]
    source_key: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
