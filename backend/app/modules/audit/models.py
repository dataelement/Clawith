"""Private append-only Audit persistence model."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_audit_records_tenant_id_id"),
        CheckConstraint(
            "actor_kind IN ('membership', 'platform_account', 'agent', 'system')",
            name="ck_audit_records_actor_kind",
        ),
        CheckConstraint(
            "(actor_kind = 'membership' AND membership_id IS NOT NULL "
            "AND platform_account_id IS NULL AND agent_id IS NULL AND run_id IS NULL AND system_component IS NULL) OR "
            "(actor_kind = 'platform_account' AND membership_id IS NULL "
            "AND platform_account_id IS NOT NULL AND agent_id IS NULL AND run_id IS NULL AND system_component IS NULL) OR "
            "(actor_kind = 'agent' AND membership_id IS NULL "
            "AND platform_account_id IS NULL AND agent_id IS NOT NULL AND system_component IS NULL) OR "
            "(actor_kind = 'system' AND membership_id IS NULL "
            "AND platform_account_id IS NULL AND agent_id IS NULL AND run_id IS NULL AND system_component IS NOT NULL)",
            name="ck_audit_records_actor_shape",
        ),
        CheckConstraint("outcome IN ('succeeded', 'failed', 'denied')", name="ck_audit_records_outcome"),
        CheckConstraint("metadata_schema_version > 0", name="ck_audit_records_metadata_version"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "audit"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    membership_id: Mapped[UUID | None]
    platform_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    agent_id: Mapped[UUID | None]
    run_id: Mapped[UUID | None]
    system_component: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    target_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_schema_version: Mapped[int] = mapped_column(nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
