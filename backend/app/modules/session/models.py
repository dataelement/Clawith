"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "agent_id", "id"),
        UniqueConstraint("tenant_id", "agent_id", "membership_id", "id"),
        CheckConstraint("next_position > 0 AND goal_configuration_version > 0", name="ck_sessions_versions"),
        CheckConstraint("NOT goal_enabled OR goal_input_id IS NOT NULL", name="ck_sessions_goal_input"),
        ForeignKeyConstraint(
            ["tenant_id", "id", "goal_input_id", "goal_input_kind"],
            ["session_entries.tenant_id", "session_entries.session_id", "session_entries.id", "session_entries.kind"],
            name="fk_sessions_goal_input",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"info": {"owner": "session"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    membership_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    next_position: Mapped[int]
    goal_enabled: Mapped[bool]
    goal_input_id: Mapped[UUID | None]
    goal_input_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    goal_configuration_version: Mapped[int]
    goal_configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SessionEntryRecord(Base):
    __tablename__ = "session_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "session_id"],
            ["sessions.tenant_id", "sessions.agent_id", "sessions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "session_id", "position"),
        UniqueConstraint("tenant_id", "session_id", "id"),
        UniqueConstraint("tenant_id", "session_id", "id", "kind"),
        UniqueConstraint("tenant_id", "agent_id", "id", "kind"),
        UniqueConstraint("tenant_id", "session_id", "source_key"),
        UniqueConstraint("tenant_id", "session_id", "message_key"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id", "source_run_id", "origin_input_id"],
            ["session_run_links.tenant_id", "session_run_links.session_id", "session_run_links.run_id", "session_run_links.input_id"],
            name="fk_session_entries_source_run", ondelete="RESTRICT", use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id", "origin_input_id", "origin_input_kind"],
            ["session_entries.tenant_id", "session_entries.session_id", "session_entries.id", "session_entries.kind"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "related_waiting_run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('input', 'reply')", name="ck_session_entries_kind"),
        CheckConstraint("source_run_id IS NULL OR kind = 'reply'", name="ck_session_entries_execution_source"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id", "related_waiting_run_id"],
            ["session_run_links.tenant_id", "session_run_links.session_id", "session_run_links.run_id"],
            name="fk_session_entries_waiting_run_link",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("position > 0 AND payload_version > 0", name="ck_session_entries_versions"),
        CheckConstraint(
            "(kind = 'input' AND source_key IS NOT NULL AND origin_input_id IS NULL) OR (kind = 'reply' AND source_key IS NULL AND origin_input_id IS NOT NULL AND related_waiting_run_id IS NULL)",
            name="ck_session_entries_origin",
        ),
        CheckConstraint(
            "num_nonnulls(related_waiting_run_id, waiting_reference) IN (0, 2)",
            name="ck_session_entries_wait_reference",
        ),
        {"info": {"owner": "session"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    position: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str | None] = mapped_column(String(512))
    message_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin_input_id: Mapped[UUID | None]
    origin_input_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    related_waiting_run_id: Mapped[UUID | None]
    waiting_reference: Mapped[str | None] = mapped_column(String(512))
    payload_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SessionRunLinkRecord(Base):
    __tablename__ = "session_run_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "session_id"],
            ["sessions.tenant_id", "sessions.agent_id", "sessions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id", "input_id", "input_kind"],
            ["session_entries.tenant_id", "session_entries.session_id", "session_entries.id", "session_entries.kind"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.agent_id", "agent_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "session_id", "source_key"),
        UniqueConstraint("tenant_id", "run_id"),
        UniqueConstraint("tenant_id", "session_id", "run_id"),
        UniqueConstraint("tenant_id", "session_id", "run_id", "input_id"),
        CheckConstraint("history_cutoff > 0 AND result_version > 0", name="ck_session_run_links_versions"),
        CheckConstraint("admission IN ('pending', 'started', 'failed')", name="ck_session_run_links_admission"),
        CheckConstraint("(admission = 'started') = (run_id IS NOT NULL)", name="ck_session_run_links_started"),
        {"info": {"owner": "session"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    input_id: Mapped[UUID]
    input_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    source_key: Mapped[str] = mapped_column(String(512))
    history_cutoff: Mapped[int]
    run_id: Mapped[UUID | None]
    admission: Mapped[str] = mapped_column(String(16))
    admission_error: Mapped[str | None] = mapped_column(String(512))
    result_version: Mapped[int]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class SessionAttachmentRecord(Base):
    __tablename__ = "session_attachments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "session_id"], ["sessions.tenant_id", "sessions.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "uploader_membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "session_id", "origin_input_id", "origin_input_kind"],
            ["session_entries.tenant_id", "session_entries.session_id", "session_entries.id", "session_entries.kind"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "session_id", "upload_source_key"),
        CheckConstraint("byte_size >= 0 AND byte_size <= 4194304", name="ck_session_attachment_size"),
        CheckConstraint("num_nonnulls(storage_revision, published_at) IN (0, 2)", name="ck_session_attachment_publication"),
        CheckConstraint("cleanup_claimed_at IS NULL OR origin_input_id IS NULL", name="ck_session_attachment_cleanup"),
        Index("ix_session_attachment_unbound", "id", postgresql_where=text("origin_input_id IS NULL")),
        {"info": {"owner": "session"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    session_id: Mapped[UUID]
    uploader_membership_id: Mapped[UUID]
    upload_source_key: Mapped[str] = mapped_column(String(512))
    origin_input_id: Mapped[UUID | None]
    origin_input_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
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
