import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WorkspaceProject(Base):
    __tablename__ = "workspace_projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_human: Mapped[str | None] = mapped_column(String(200), nullable=True)
    built_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    deploy_type: Mapped[str | None] = mapped_column(
        Enum("static", "container", name="deploy_type_enum", create_constraint=False),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "requested", "building", "awaiting_approval", "deployed",
            "failed", "rejected", "stopped", "undeployed",
            name="workspace_status_enum", create_constraint=False,
        ),
        nullable=False,
        default="requested",
    )
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    container_image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    container_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_endpoint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resource_limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    auto_fix_attempts: Mapped[int] = mapped_column(Integer, default=0)
    auto_fix_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bug_reports: Mapped[list["WorkspaceBugReport"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class WorkspaceBugReport(Base):
    __tablename__ = "workspace_bug_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        Enum("health_check", "user_report", name="bug_source_enum", create_constraint=False),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "open", "investigating", "fixed", "escalated",
            name="bug_status_enum", create_constraint=False,
        ),
        nullable=False,
        default="open",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["WorkspaceProject"] = relationship(back_populates="bug_reports")
