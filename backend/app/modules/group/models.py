"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class GroupRecord(Base):
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint("next_position > 0", name="ck_groups_next_position"),
        {"info": {"owner": "group"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str] = mapped_column(String(200))
    announcement: Mapped[str] = mapped_column(String(16384))
    next_position: Mapped[int]
    enabled: Mapped[bool]


class GroupMembershipRecord(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "group_id", "membership_id"),
        {"info": {"owner": "group"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    group_id: Mapped[UUID]
    membership_id: Mapped[UUID]
    enabled: Mapped[bool]


class GroupEventRecord(Base):
    __tablename__ = "group_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "group_id", "position"),
        UniqueConstraint("tenant_id", "group_id", "source_key"),
        UniqueConstraint("tenant_id", "group_id", "id", "kind"),
        UniqueConstraint("tenant_id", "agent_id", "id", "kind"),
        ForeignKeyConstraint(
            ["tenant_id", "group_id", "origin_event_id", "origin_event_kind"],
            ["group_events.tenant_id", "group_events.group_id", "group_events.id", "group_events.kind"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('input', 'reply')", name="ck_group_events_kind"),
        CheckConstraint(
            "(kind = 'input' AND source_key IS NOT NULL AND origin_event_id IS NULL AND agent_id IS NULL) OR (kind = 'reply' AND source_key IS NULL AND origin_event_id IS NOT NULL AND agent_id IS NOT NULL AND membership_id IS NULL)",
            name="ck_group_events_shape",
        ),
        CheckConstraint("position > 0 AND payload_version > 0", name="ck_group_events_versions"),
        {"info": {"owner": "group"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    group_id: Mapped[UUID]
    position: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str | None] = mapped_column(String(512))
    membership_id: Mapped[UUID | None]
    agent_id: Mapped[UUID | None]
    origin_event_id: Mapped[UUID | None]
    origin_event_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    payload_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class GroupRunLinkRecord(Base):
    __tablename__ = "group_run_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "group_id", "event_id", "event_kind"],
            ["group_events.tenant_id", "group_events.group_id", "group_events.id", "group_events.kind"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "event_id", "agent_id"),
        UniqueConstraint("tenant_id", "run_id"),
        CheckConstraint("result_version > 0", name="ck_group_run_links_version"),
        CheckConstraint("admission IN ('pending', 'started', 'failed')", name="ck_group_run_links_admission"),
        CheckConstraint("(admission = 'started') = (run_id IS NOT NULL)", name="ck_group_run_links_started"),
        {"info": {"owner": "group"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    group_id: Mapped[UUID]
    event_id: Mapped[UUID]
    event_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    agent_id: Mapped[UUID]
    run_id: Mapped[UUID | None]
    admission: Mapped[str] = mapped_column(String(16))
    admission_error: Mapped[str | None] = mapped_column(String(512))
    result_version: Mapped[int]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
