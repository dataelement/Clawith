"""Pydantic schemas for request/response validation."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ─── Auth ───────────────────────────────────────────────

class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None
    tenant_id: str | None = None  # UUID string of the company
    invitation_code: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    display_name: str
    avatar_url: str | None = None
    role: str
    tenant_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    title: str | None = None
    feishu_open_id: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    title: str | None = None
    department_id: uuid.UUID | None = None


# ─── Agent ──────────────────────────────────────────────

class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role_description: str = Field(default="", max_length=500)
    bio: str | None = None
    avatar_url: str | None = None
    # Soul
    personality: str = ""
    boundaries: str = ""
    # Model
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    # Permissions
    permission_scope_type: str = "company"  # company | user
    permission_scope_ids: list[uuid.UUID] = []
    permission_access_level: str = "use"  # use | manage
    # Template
    template_id: uuid.UUID | None = None
    # Autonomy
    autonomy_policy: dict | None = None
    # Token limits
    max_tokens_per_day: int | None = None
    max_tokens_per_month: int | None = None
    # Skills to copy into agent workspace
    skill_ids: list[uuid.UUID] = []


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    avatar_url: str | None = None
    role_description: str
    bio: str | None = None
    status: str
    creator_id: uuid.UUID
    creator_username: str | None = None  # Populated by API layer; not in ORM model directly
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    autonomy_policy: dict
    tokens_used_today: int
    tokens_used_month: int
    max_tokens_per_day: int | None = None
    max_tokens_per_month: int | None = None
    max_tool_rounds: int = 50
    heartbeat_enabled: bool = True
    heartbeat_interval_minutes: int = 30
    heartbeat_active_hours: str = "09:00-18:00"
    last_heartbeat_at: datetime | None = None
    timezone: str | None = None
    expires_at: datetime | None = None
    is_expired: bool = False
    llm_calls_today: int = 0
    max_llm_calls_per_day: int = 100
    created_at: datetime
    last_active_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentUpdate(BaseModel):
    name: str | None = None
    role_description: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    autonomy_policy: dict | None = None
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    max_tokens_per_day: int | None = None
    max_tokens_per_month: int | None = None
    max_tool_rounds: int | None = None
    heartbeat_enabled: bool | None = None
    heartbeat_interval_minutes: int | None = None
    heartbeat_active_hours: str | None = None
    timezone: str | None = None
    expires_at: datetime | None = None  # Admin only — extend agent expiry


class AgentStatusOut(BaseModel):
    """Agent status from state.json."""
    agent_id: uuid.UUID
    name: str
    status: str
    current_task: str | None = None
    last_active: datetime | None = None
    channel_status: dict = {}
    stats: dict = {}


# ─── Task ───────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    type: str = "todo"  # todo | supervision
    priority: str = "medium"
    due_date: datetime | None = None
    # Supervision fields
    supervision_target_name: str | None = None
    supervision_channel: str | None = None
    remind_schedule: str | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    title: str
    description: str | None = None
    type: str
    status: str
    priority: str
    assignee: str
    created_by: uuid.UUID
    creator_username: str | None = None
    due_date: datetime | None = None
    supervision_target_name: str | None = None
    supervision_channel: str | None = None
    remind_schedule: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    due_date: datetime | None = None
    supervision_target_name: str | None = None
    remind_schedule: str | None = None


class TaskLogCreate(BaseModel):
    content: str


class TaskLogOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Department ─────────────────────────────────────────

class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DepartmentTree(DepartmentOut):
    children: list["DepartmentTree"] = []
    member_count: int = 0


# ─── LLM ────────────────────────────────────────────────

class LLMModelCreate(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    label: str
    max_tokens_per_day: int | None = None
    enabled: bool = True
    supports_vision: bool = False


class LLMModelOut(BaseModel):
    id: uuid.UUID
    provider: str
    model: str
    base_url: str | None = None
    label: str
    max_tokens_per_day: int | None = None
    enabled: bool
    supports_vision: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Channel Config ─────────────────────────────────────

class ChannelConfigCreate(BaseModel):
    channel_type: str = "feishu"
    app_id: str
    app_secret: str
    encrypt_key: str | None = None
    verification_token: str | None = None


class ChannelConfigOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    channel_type: str
    app_id: str | None = None
    app_secret: str | None = None
    encrypt_key: str | None = None
    is_configured: bool
    is_connected: bool
    last_tested_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Approval ───────────────────────────────────────────

class ApprovalRequestOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    action_type: str
    details: dict
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class ApprovalAction(BaseModel):
    action: str  # "approve" | "reject"


# ─── Enterprise Info ────────────────────────────────────

class EnterpriseInfoUpdate(BaseModel):
    content: dict
    visible_roles: list[str] = []


class EnterpriseInfoOut(BaseModel):
    id: uuid.UUID
    info_type: str
    content: dict
    version: int
    visible_roles: list
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Chat ───────────────────────────────────────────────

class ChatMessageOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSend(BaseModel):
    content: str = Field(min_length=1)


# ─── Audit Log ──────────────────────────────────────────

class AuditLogOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    action: str
    details: dict
    ip_address: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Generic ────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int = 1
    page_size: int = 20


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
