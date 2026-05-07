"""Pydantic schemas for the Project feature."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Project CRUD ────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    # MVP only supports scope_type='tenant'; the backend ignores anything else
    scope_type: Literal["tenant"] = "tenant"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    chat_visibility: Literal["shared", "private"] | None = None


class ProjectAgentSummary(BaseModel):
    """Compact agent reference for list / detail cards."""

    agent_id: uuid.UUID
    name: str
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class ProjectListItem(BaseModel):
    """Row in the ProjectsList page."""

    id: uuid.UUID
    name: str
    description: str
    scope_type: str
    scope_id: uuid.UUID
    chat_visibility: str
    archived_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Aggregates for the card
    agent_count: int = 0
    file_count: int = 0
    session_count: int = 0
    last_message_at: datetime | None = None
    agents: list[ProjectAgentSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ProjectOut(ProjectListItem):
    """Full detail response."""

    # Currently identical to ProjectListItem — room to add owner info, etc.
    pass


# ── Project agents (membership) ─────────────────────────────────────────

class ProjectAgentAdd(BaseModel):
    agent_id: uuid.UUID


class ProjectAgentOut(BaseModel):
    project_id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    avatar_url: str | None = None
    added_by: uuid.UUID
    added_at: datetime

    model_config = {"from_attributes": True}


# ── Project files ───────────────────────────────────────────────────────

class ProjectFileOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    # Path relative to the project workspace root, "/"-separated.
    # Same as filename for files at root; "posts/draft.md" for nested ones.
    path: str = ""
    is_dir: bool = False
    size_bytes: int
    mime_type: str
    created_by_type: Literal["user", "agent"]
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Number of project tasks that link to this file (Phase 3 deliverables).
    # Zero for directories and unlinked files.
    linked_task_count: int = 0
    # Up to 3 task titles for tooltip display. UI calls /tasks for the full list.
    linked_task_titles: list[str] = []

    model_config = {"from_attributes": True}


class ProjectFileContent(BaseModel):
    """Text content of a single file (for inline editor / preview)."""

    path: str
    content: str


class ProjectFileWrite(BaseModel):
    """Body for PUT /files/content — used by FileBrowser for new file / edit / new folder (.gitkeep)."""

    path: str = Field(..., min_length=1, max_length=500)
    content: str = Field(default="", max_length=2_000_000)  # 2 MB text cap


class ProjectFileMove(BaseModel):
    """Body for POST /files/move — drag-to-folder relocation or rename."""

    src_path: str = Field(..., min_length=1, max_length=500)
    dst_path: str = Field(..., min_length=1, max_length=500)


class ProjectFileConflict(BaseModel):
    """Returned with HTTP 409 when an upload filename already exists."""

    detail: str = "filename_conflict"
    existing: ProjectFileOut
    # Hint: retry with ?conflict=replace|keep_both
    suggested_alt_name: str  # e.g. "brand (1).pdf"


# ── Brief ───────────────────────────────────────────────────────────────

class ProjectBriefOut(BaseModel):
    content: str


class ProjectBriefUpdate(BaseModel):
    content: str = Field(..., max_length=200_000)  # ~ a very generous cap


# ── Chat sessions inside a project ──────────────────────────────────────

# ── Scheduled tasks ─────────────────────────────────────────────────────

ScheduledTaskFrequency = Literal["hourly", "daily", "weekdays", "weekly"]


class ProjectScheduledTaskCreate(BaseModel):
    agent_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=100)
    prompt: str = Field(..., min_length=1, max_length=4000)
    frequency: ScheduledTaskFrequency
    is_enabled: bool = True


class ProjectScheduledTaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    frequency: ScheduledTaskFrequency | None = None
    is_enabled: bool | None = None


class ProjectScheduledTaskOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    agent_avatar_url: str | None = None
    name: str
    prompt: str
    frequency: ScheduledTaskFrequency
    is_enabled: bool
    last_fired_at: datetime | None = None
    fire_count: int
    cron_expr: str  # derived from frequency; shown for transparency
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectChatSessionOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    user_id: uuid.UUID
    user_display_name: str | None = None
    title: str
    created_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0
    # True for the requesting user's own session; else they see it read-only
    owned_by_me: bool = True

    model_config = {"from_attributes": True}
