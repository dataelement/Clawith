"""Owner-private S2 persistence records."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint("num_nonnulls(membership_id, agent_id, group_id) = 1", name="ck_workspaces_one_owner"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "membership_id"),
        UniqueConstraint("tenant_id", "agent_id"),
        UniqueConstraint("tenant_id", "group_id"),
        {"info": {"owner": "workspace"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    membership_id: Mapped[UUID | None]
    agent_id: Mapped[UUID | None]
    group_id: Mapped[UUID | None]


class SkillPackageRecord(Base):
    __tablename__ = "skill_packages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "id", "ownership_scope", "ownership_key"),
        ForeignKeyConstraint(["tenant_id", "owner_agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_item_id", "catalog_kind"],
            ["capability_catalog_items.tenant_id", "capability_catalog_items.id", "capability_catalog_items.kind"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("format_version > 0", name="ck_skill_packages_format_version"),
        Index(
            "uq_skill_packages_shared_catalog",
            "tenant_id",
            "catalog_item_id",
            unique=True,
            postgresql_where=text("owner_agent_id IS NULL AND catalog_item_id IS NOT NULL"),
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_skill_packages_content_hash"),
        {"info": {"owner": "workspace"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    owner_agent_id: Mapped[UUID | None]
    ownership_scope: Mapped[str] = mapped_column(
        String(16), Computed("CASE WHEN owner_agent_id IS NULL THEN 'shared' ELSE 'private' END", persisted=True)
    )
    ownership_key: Mapped[UUID] = mapped_column(Computed("COALESCE(owner_agent_id, tenant_id)", persisted=True))
    catalog_item_id: Mapped[UUID | None]
    catalog_kind: Mapped[str] = mapped_column(String(16), Computed("'skill'", persisted=True))
    storage_key: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    format_version: Mapped[int]
    revision: Mapped[str] = mapped_column(String(128))


class AgentSkillBindingRecord(Base):
    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "agent_id", "skill_name"),
        CheckConstraint("package_scope IN ('shared', 'private')", name="ck_agent_skill_bindings_scope"),
        CheckConstraint("skill_name ~ '^[a-z][a-z0-9_-]{0,63}$'", name="ck_agent_skill_bindings_name"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "package_id", "package_scope", "package_ownership_key"],
            [
                "skill_packages.tenant_id",
                "skill_packages.id",
                "skill_packages.ownership_scope",
                "skill_packages.ownership_key",
            ],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "workspace"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    skill_name: Mapped[str] = mapped_column(String(64))
    package_id: Mapped[UUID]
    package_scope: Mapped[str] = mapped_column(String(16))
    package_ownership_key: Mapped[UUID] = mapped_column(
        Computed("CASE WHEN package_scope = 'shared' THEN tenant_id ELSE agent_id END", persisted=True)
    )
