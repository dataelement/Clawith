"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class GroupRecord(Base):
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint("next_position > 0", name="ck_groups_next_position"),
        ForeignKeyConstraint(["tenant_id", "created_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        {"info": {"owner": "group"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str] = mapped_column(String(200))
    created_by_membership_id: Mapped[UUID | None] = mapped_column(nullable=True)
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
        ForeignKeyConstraint(["tenant_id", "group_id", "conversation_id"],
            ["group_conversations.tenant_id", "group_conversations.group_id", "group_conversations.id"], ondelete="RESTRICT"),
        Index("ix_group_conversation_events", "tenant_id", "group_id", "conversation_id", "position"),
        UniqueConstraint("tenant_id", "group_id", "source_key"),
        UniqueConstraint("tenant_id", "group_id", "message_key"),
        ForeignKeyConstraint(
            ["tenant_id", "group_id", "agent_id", "origin_event_id", "source_run_id", "conversation_id"],
            ["group_run_links.tenant_id", "group_run_links.group_id", "group_run_links.agent_id",
             "group_run_links.event_id", "group_run_links.run_id", "group_run_links.conversation_id"],
            name="fk_group_events_source_run", ondelete="RESTRICT", use_alter=True,
        ),
        UniqueConstraint("tenant_id", "group_id", "id", "kind"),
        UniqueConstraint("tenant_id", "group_id", "id", "conversation_id"),
        UniqueConstraint("tenant_id", "id", "kind"),
        UniqueConstraint("tenant_id", "agent_id", "id", "kind"),
        ForeignKeyConstraint(
            ["tenant_id", "group_id", "origin_event_id", "origin_event_kind"],
            ["group_events.tenant_id", "group_events.group_id", "group_events.id", "group_events.kind"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('input', 'reply')", name="ck_group_events_kind"),
        CheckConstraint("source_run_id IS NULL OR (kind = 'reply' AND conversation_id IS NOT NULL)",
            name="ck_group_events_execution_source"),
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
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    position: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str | None] = mapped_column(String(512))
    message_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
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
        ForeignKeyConstraint(["tenant_id", "group_id", "conversation_id"],
            ["group_conversations.tenant_id", "group_conversations.group_id", "group_conversations.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "run_id"),
        UniqueConstraint("tenant_id", "group_id", "run_id"),
        UniqueConstraint("tenant_id", "group_id", "agent_id", "event_id", "run_id", "conversation_id"),
        ForeignKeyConstraint(["tenant_id", "group_id", "event_id", "conversation_id"],
            ["group_events.tenant_id", "group_events.group_id", "group_events.id", "group_events.conversation_id"],
            ondelete="RESTRICT"),
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
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    event_id: Mapped[UUID]
    event_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    agent_id: Mapped[UUID]
    run_id: Mapped[UUID | None]
    admission: Mapped[str] = mapped_column(String(16))
    admission_error: Mapped[str | None] = mapped_column(String(512))
    result_version: Mapped[int]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class GroupAttachmentRecord(Base):
    __tablename__ = "group_attachments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "uploader_membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_id", "origin_event_id", "origin_event_kind"],
            ["group_events.tenant_id", "group_events.group_id", "group_events.id", "group_events.kind"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "group_id", "upload_source_key"),
        CheckConstraint("byte_size >= 0 AND byte_size <= 4194304", name="ck_group_attachment_size"),
        CheckConstraint("num_nonnulls(storage_revision, published_at) IN (0, 2)", name="ck_group_attachment_publication"),
        CheckConstraint("cleanup_claimed_at IS NULL OR origin_event_id IS NULL", name="ck_group_attachment_cleanup"),
        Index("ix_group_attachment_unbound", "id", postgresql_where=text("origin_event_id IS NULL")),
        {"info": {"owner": "group"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    group_id: Mapped[UUID]
    uploader_membership_id: Mapped[UUID]
    upload_source_key: Mapped[str] = mapped_column(String(512))
    origin_event_id: Mapped[UUID | None]
    origin_event_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(256))
    byte_size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1024))
    storage_revision: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unbound_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleanup_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupAgentRecord(Base):
    __tablename__ = "group_agents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "group_id", "agent_id"),
        {"info": {"owner": "group"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    group_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    enabled: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupConversationRecord(Base):
    __tablename__ = "group_conversations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "created_by_membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "group_id", "id"),
        Index("uq_group_default_conversation", "tenant_id", "group_id", unique=True, postgresql_where=text("is_default")),
        CheckConstraint("NOT is_default OR enabled", name="ck_group_default_conversation_enabled"),
        {"info": {"owner": "group"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    group_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool]
    enabled: Mapped[bool]
    created_by_membership_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupReadRecord(Base):
    __tablename__ = "group_reads"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "group_id", "conversation_id"],
            ["group_conversations.tenant_id", "group_conversations.group_id", "group_conversations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "conversation_id", "membership_id"),
        CheckConstraint("through_position >= 0", name="ck_group_read_position"),
        {"info": {"owner": "group"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    group_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    membership_id: Mapped[UUID]
    through_position: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
