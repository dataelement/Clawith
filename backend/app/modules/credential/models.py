"""Private Credential persistence model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class CredentialRecord(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_credentials_tenant_id_id"),
        UniqueConstraint("tenant_id", "id", "owner_kind", name="uq_credentials_tenant_id_owner_kind"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "owner_kind",
            "owner_id",
            name="uq_credentials_binding_identity",
        ),
        CheckConstraint(
            "membership_owner_id IS NULL OR agent_owner_id IS NULL",
            name="ck_credentials_at_most_one_subject_owner",
        ),
        CheckConstraint("payload_version > 0", name="ck_credentials_payload_version"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_owner_id"],
            ["memberships.tenant_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_owner_id"],
            ["agents.tenant_id", "agents.id"],
            name="fk_credentials_agent_owner",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"info": {"owner": "credential"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    membership_owner_id: Mapped[UUID | None]
    agent_owner_id: Mapped[UUID | None]
    owner_kind: Mapped[str] = mapped_column(
        String(16),
        Computed(
            "CASE WHEN membership_owner_id IS NOT NULL THEN 'membership' "
            "WHEN agent_owner_id IS NOT NULL THEN 'agent' ELSE 'tenant' END",
            persisted=True,
        ),
    )
    owner_id: Mapped[UUID] = mapped_column(
        Computed("COALESCE(membership_owner_id, agent_owner_id, tenant_id)", persisted=True)
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_version: Mapped[int] = mapped_column(nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
