"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class A2ARequestRecord(Base):
    __tablename__ = "a2a_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "source_agent_id", "delivery_run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"], ondelete="RESTRICT"),
        CheckConstraint("temp_files_version > 0 AND jsonb_typeof(temp_files_manifest) = 'object' AND octet_length(temp_files_manifest::text) <= 65536",
            name="ck_a2a_temp_files_manifest"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "source_agent_id", "source_run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["tenant_id", "target_agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "target_agent_id", "target_run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "source_run_id", "source_call_id"),
        UniqueConstraint("tenant_id", "target_run_id"),
        CheckConstraint("intent IN ('notify', 'consult', 'task_delegate')", name="ck_a2a_requests_intent"),
        CheckConstraint(
            "payload_version > 0 AND delegation_version > 0 AND result_version > 0", name="ck_a2a_requests_versions"
        ),
        CheckConstraint("admission IN ('pending', 'started', 'failed')", name="ck_a2a_requests_admission"),
        CheckConstraint("(admission = 'started') = (target_run_id IS NOT NULL)", name="ck_a2a_requests_started"),
        CheckConstraint(
            "source_delivery IN ('not_required', 'awaiting_result', 'pending', 'accepted', 'source_terminal')",
            name="ck_a2a_requests_delivery",
        ),
        CheckConstraint(
            "(intent = 'notify') = (source_delivery = 'not_required')", name="ck_a2a_requests_notify_delivery"
        ),
        CheckConstraint(
            "source_delivery NOT IN ('pending', 'accepted') OR (result IS NOT NULL AND jsonb_typeof(result) = 'object')",
            name="ck_a2a_requests_result_delivery",
        ),
        {"info": {"owner": "a2a"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_agent_id: Mapped[UUID]
    source_run_id: Mapped[UUID]
    delivery_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    temp_files_version: Mapped[int] = mapped_column(default=1, server_default="1")
    temp_files_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    source_call_id: Mapped[str] = mapped_column(String(256))
    target_agent_id: Mapped[UUID]
    target_run_id: Mapped[UUID | None]
    intent: Mapped[str] = mapped_column(String(32))
    payload_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delegation_version: Mapped[int]
    delegated_connections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    admission: Mapped[str] = mapped_column(String(16))
    admission_error: Mapped[str | None] = mapped_column(String(512))
    result_version: Mapped[int]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    source_delivery: Mapped[str] = mapped_column(String(32))
