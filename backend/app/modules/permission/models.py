"""Private Permission persistence models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AgentVisibilityRecord(Base):
    __tablename__ = "agent_visibilities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_visibilities_tenant_id_id"),
        UniqueConstraint("tenant_id", "agent_id", name="uq_agent_visibilities_tenant_agent"),
        CheckConstraint("visibility IN ('tenant', 'restricted')", name="ck_agent_visibilities_visibility"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"
        ),
        {"info": {"owner": "permission"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentVisibilityGrantRecord(Base):
    __tablename__ = "agent_visibility_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_visibility_grants_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "grantee_kind",
            "grantee_id",
            name="uq_agent_visibility_grants_grantee",
        ),
        CheckConstraint(
            "(membership_id IS NOT NULL)::integer + (source_agent_id IS NOT NULL)::integer = 1",
            name="ck_agent_visibility_grants_one_grantee",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "permission"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    membership_id: Mapped[UUID | None]
    source_agent_id: Mapped[UUID | None]
    grantee_kind: Mapped[str] = mapped_column(
        String(16),
        Computed(
            "CASE WHEN membership_id IS NOT NULL THEN 'membership' ELSE 'agent' END",
            persisted=True,
        ),
    )
    grantee_id: Mapped[UUID] = mapped_column(
        Computed("COALESCE(membership_id, source_agent_id)", persisted=True)
    )
    granted_by_membership_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
