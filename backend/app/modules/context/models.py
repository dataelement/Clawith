"""Private replaceable Context projection schema."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ContextProjectionRecord(Base):
    __tablename__ = "run_context_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", name="uq_run_context_projections_tenant_run"),
        CheckConstraint("payload_schema_version > 0", name="ck_run_context_projections_payload_version"),
        CheckConstraint("coverage_sequence >= 0", name="ck_run_context_projections_coverage_sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["agent_runs.tenant_id", "agent_runs.id"], ondelete="RESTRICT"
        ),
        {"info": {"owner": "context"}},
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    coverage_sequence: Mapped[int] = mapped_column(nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
