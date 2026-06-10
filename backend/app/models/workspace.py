"""Workspace collaboration models.

These tables track file revisions and short-lived human editing locks for
agent workspaces. The actual files remain on disk; the database stores the
change history needed for diff viewing and rollback.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkspaceFileRevision(Base):
    """A single meaningful workspace file revision."""

    __tablename__ = "workspace_file_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False, default="write")
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user | agent | system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    before_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    group_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceEditLock(Base):
    """Short-lived lock while a human is actively editing a workspace file."""

    __tablename__ = "workspace_edit_locks"
    __table_args__ = (
        UniqueConstraint("agent_id", "path", name="uq_workspace_edit_locks_agent_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
