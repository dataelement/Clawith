"""Private Model configuration persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ModelRecord(Base):
    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_llm_models_tenant_id_id"),
        CheckConstraint("credential_owner_kind = 'tenant'", name="ck_llm_models_tenant_credential"),
        CheckConstraint("context_limit > 0", name="ck_llm_models_context_limit"),
        CheckConstraint("output_limit > 0 AND output_limit <= context_limit", name="ck_llm_models_output_limit"),
        CheckConstraint("settings_version > 0", name="ck_llm_models_settings_version"),
        CheckConstraint(
            "capability_source IN ('provider_metadata', 'builtin_catalog', 'administrator')",
            name="ck_llm_models_capability_source",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "credential_owner_kind"],
            ["credentials.tenant_id", "credentials.id", "credentials.owner_kind"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "model"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    credential_id: Mapped[UUID] = mapped_column(nullable=False)
    credential_owner_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="tenant")
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    context_limit: Mapped[int] = mapped_column(nullable=False)
    output_limit: Mapped[int] = mapped_column(nullable=False)
    capability_source: Mapped[str] = mapped_column(String(32), nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    settings_version: Mapped[int] = mapped_column(nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantModelDefaultRecord(Base):
    __tablename__ = "tenant_model_defaults"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_model_defaults_tenant_id_id"),
        UniqueConstraint("tenant_id", name="uq_tenant_model_defaults_tenant"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "model_id"],
            ["llm_models.tenant_id", "llm_models.id"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "model"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    model_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderContinuationRecord(Base):
    __tablename__ = "provider_continuation_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_provider_continuations_tenant_id_id"),
        UniqueConstraint("tenant_id", "run_id", "model_id", name="uq_provider_continuations_run_model"),
        CheckConstraint("payload_schema_version > 0", name="ck_provider_continuations_payload_version"),
        CheckConstraint("encryption_version > 0", name="ck_provider_continuations_encryption_version"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["agent_runs.tenant_id", "agent_runs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "model_id"], ["llm_models.tenant_id", "llm_models.id"], ondelete="RESTRICT"
        ),
        {"info": {"owner": "model"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    model_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(nullable=False)
    encryption_version: Mapped[int] = mapped_column(nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
