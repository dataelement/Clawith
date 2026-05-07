"""Project Task models — per-project deliverable / to-do tracking.

Distinct from `app.models.task.Task` (which is agent-scoped + supervision
flavored). A ProjectTask lives at project scope and is the anchor for
multi-agent goal-tracking: agents read the active list via system-prompt
injection and mutate state via the four `*_project_task*` tools.
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectTaskStatus(str, Enum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"
    BLOCKED = "blocked"


class ProjectTaskCreatedByType(str, Enum):
    USER = "user"
    AGENT = "agent"


class ProjectTask(Base):
    """A deliverable / to-do for a project.

    `created_by_type='agent'` rows come from the `create_project_task` tool;
    `assigned_agent_id` lets an agent claim a task during chat. `completed_at`
    is filled automatically by the API when status moves to 'done'.
    """

    __tablename__ = "project_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ProjectTaskStatus.TODO.value, index=True)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_by_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ProjectTaskCreatedByType.USER.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectTaskFile(Base):
    """M:N junction between project_tasks and project_files.

    A file may anchor multiple tasks (e.g. a brand guide referenced by both
    'design poster' and 'write copy' tasks), and tasks are often created after
    a file already exists, so we keep an explicit junction rather than
    extending project_files with a single task_id column.
    """

    __tablename__ = "project_task_files"

    project_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    project_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_files.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    linked_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
    linked_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
