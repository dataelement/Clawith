"""Native group chat domain models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Group(Base):
    """A tenant-owned, long-lived native group chat."""

    __tablename__ = "groups"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_groups"),
        Index("ix_groups_tenant_id_deleted_at", "tenant_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_groups_tenant_id_tenants", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_groups_created_by_participant_id_participants",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GroupMember(Base):
    """A participant's reusable membership record in a native group."""

    __tablename__ = "group_members"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_group_members"),
        CheckConstraint("role IN ('manager', 'member')", name="ck_group_members_role"),
        UniqueConstraint("group_id", "participant_id", name="uq_group_members_group_participant"),
        Index("ix_group_members_participant_id", "participant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_group_members_group_id_groups", ondelete="CASCADE"),
        nullable=False,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_group_members_participant_id_participants",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member", server_default=text("'member'")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_read_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
