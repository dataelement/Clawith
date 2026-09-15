"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AgentTriggerRecord(Base):
    __tablename__ = "agent_triggers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "agent_id", "id"),
        CheckConstraint("configuration_version > 0 AND delegation_version > 0", name="ck_agent_triggers_versions"),
        {"info": {"owner": "trigger"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    configuration_version: Mapped[int]
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delegation_version: Mapped[int]
    delegated_connections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    enabled: Mapped[bool]


class TriggerOccurrenceRecord(Base):
    __tablename__ = "trigger_occurrences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "trigger_id"],
            ["agent_triggers.tenant_id", "agent_triggers.agent_id", "agent_triggers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "trigger_id", "source_key"),
        UniqueConstraint("tenant_id", "run_id"),
        CheckConstraint("payload_version > 0 AND result_version > 0", name="ck_trigger_occurrences_versions"),
        CheckConstraint("admission IN ('pending', 'started', 'failed')", name="ck_trigger_occurrences_admission"),
        CheckConstraint("(admission = 'started') = (run_id IS NOT NULL)", name="ck_trigger_occurrences_started"),
        {"info": {"owner": "trigger"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trigger_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    source_key: Mapped[str] = mapped_column(String(512))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[UUID | None]
    admission: Mapped[str] = mapped_column(String(16))
    admission_error: Mapped[str | None] = mapped_column(String(512))
    result_version: Mapped[int]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
