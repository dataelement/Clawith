"""Project models — shared workspace container for multi-agent collaboration.

A Project is distinct from an Agent's private workspace. Files uploaded to
a Project live in PROJECT_WORKSPACE_DIR/{project_id}/ and are accessible to
every agent assigned to the project when working in a chat session bound to
that project.
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectScopeType(str, Enum):
    """Who can see a project. MVP only emits TENANT; DEPARTMENT/USER reserved."""
    TENANT = "tenant"
    DEPARTMENT = "department"
    USER = "user"


class ProjectChatVisibility(str, Enum):
    """Whether chat sessions inside a project are visible to other scope members."""
    SHARED = "shared"    # Default: other scope members see sessions read-only
    PRIVATE = "private"  # Only the session's own user can see it


class ProjectFileCreatedByType(str, Enum):
    """Distinguishes human upload from agent write. UI surfaces this as an icon."""
    USER = "user"
    AGENT = "agent"


class Project(Base):
    """A shared workspace + brief + assigned agents, organised around one goal."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "name", name="uq_projects_scope_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default=ProjectScopeType.TENANT.value)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chat_visibility: Mapped[str] = mapped_column(String(20), nullable=False, default=ProjectChatVisibility.SHARED.value)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectAgent(Base):
    """M2M between projects and agents. An agent must be here to chat in a project."""

    __tablename__ = "project_agents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    added_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProjectFile(Base):
    """Metadata for files under PROJECT_WORKSPACE_DIR/{project_id}/.

    Physical bytes live on disk; this table tracks display name, size, uploader,
    and whether the file was produced by a user or by an agent at work.
    """

    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "physical_path", name="uq_project_files_project_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    physical_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
