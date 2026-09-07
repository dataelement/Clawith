"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class CapabilityCatalogItemRecord(Base):
    __tablename__ = "capability_catalog_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "id", "kind"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint("kind IN ('tool', 'mcp', 'skill')", name="ck_capability_catalog_items_kind"),
        CheckConstraint(
            "manifest_schema_version > 0 AND definition_revision > 0", name="ck_capability_catalog_items_versions"
        ),
        CheckConstraint(
            "tenant_id IS NOT NULL OR (origin_platform_item_id IS NULL AND installed_by_membership_id IS NULL AND installed_by_agent_id IS NULL)",
            name="ck_capability_catalog_items_platform_shape",
        ),
        UniqueConstraint("id", "is_platform"),
        ForeignKeyConstraint(
            ["origin_platform_item_id", "origin_is_platform"],
            ["capability_catalog_items.id", "capability_catalog_items.is_platform"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "installed_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "installed_by_agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"
        ),
        Index(
            "uq_catalog_platform_source",
            "kind",
            "source",
            "source_key",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_catalog_tenant_source",
            "tenant_id",
            "kind",
            "source",
            "source_key",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        {"info": {"owner": "capability_market"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin_platform_item_id: Mapped[UUID | None]
    is_platform: Mapped[bool] = mapped_column(Computed("tenant_id IS NULL", persisted=True))
    origin_is_platform: Mapped[bool] = mapped_column(Computed("true", persisted=True))
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(4096))
    version: Mapped[str] = mapped_column(String(128))
    manifest_schema_version: Mapped[int]
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_revision: Mapped[int]
    enabled: Mapped[bool]
    installed_by_membership_id: Mapped[UUID | None]
    installed_by_agent_id: Mapped[UUID | None]
