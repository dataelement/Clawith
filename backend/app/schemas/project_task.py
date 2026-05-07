"""Pydantic schemas for Project Tasks (Phase 3 — deliverables)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["todo", "doing", "done", "blocked"]
CreatedByType = Literal["user", "agent"]


class ProjectTaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=10000)
    status: TaskStatus = "todo"
    assigned_agent_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    due_date: datetime | None = None


class ProjectTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    status: TaskStatus | None = None
    assigned_agent_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    due_date: datetime | None = None
    # `clear_assignee` lets the client unset assignment without conflating with
    # "field omitted" semantics. Set true to null both assigned_*_id columns.
    clear_assignee: bool = False
    clear_due_date: bool = False


class ProjectTaskFileLinkRef(BaseModel):
    """One linked file inside a task detail response."""

    file_id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    linked_at: datetime
    linked_by_type: CreatedByType


class ProjectTaskOut(BaseModel):
    """Row in the Tasks tab list."""

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str
    status: TaskStatus
    assigned_agent_id: uuid.UUID | None
    assigned_agent_name: str | None
    assigned_agent_avatar_url: str | None
    assigned_user_id: uuid.UUID | None
    assigned_user_display_name: str | None
    due_date: datetime | None
    created_by: uuid.UUID
    created_by_type: CreatedByType
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    linked_file_count: int

    model_config = {"from_attributes": True}


class ProjectTaskDetail(ProjectTaskOut):
    """Detail response — superset of list row, plus the linked files themselves."""

    linked_files: list[ProjectTaskFileLinkRef] = []


class ProjectTaskFileLinkIn(BaseModel):
    file_id: uuid.UUID
