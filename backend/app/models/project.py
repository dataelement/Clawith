"""Project management models — container for agents, tasks, and shared context."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime, String, Text, ForeignKey, UniqueConstraint,
    Integer, func,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    """A named project container that groups agents and tasks under a shared goal.

    folder: flat string grouping (e.g. "2026H1", "Client A") — no nested hierarchy.
    brief:  markdown text injected into agent system prompts when working in project context (P3).
    collab_mode: 'isolated' (MVP) | 'group_chat' | 'lead_helper' (P4).
    status state machine:
        draft → active → completed → archived
        active ⇄ on_hold
        any    → archived
    """

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_projects_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brief: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # injected as system context (P3)
    folder: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    collab_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="isolated", server_default="isolated")

    target_completion_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectAgent(Base):
    """M2M: project ↔ agent. One agent can belong to multiple projects simultaneously."""

    __tablename__ = "project_agents"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member", server_default="member")
    # role: 'lead' | 'member' | 'observer'
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectTag(Base):
    """Tenant-scoped tag that can be applied to projects."""

    __tablename__ = "project_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_project_tags_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # hex or tabler color name
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectTagLink(Base):
    """M2M: project ↔ tag."""

    __tablename__ = "project_tag_links"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("project_tags.id", ondelete="CASCADE"), primary_key=True)
