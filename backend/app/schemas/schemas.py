"""Pydantic schemas for request/response validation."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ─── Auth ───────────────────────────────────────────────

class UserRegister(BaseModel):
    """Legacy combined registration - kept for backward compatibility."""
    username: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None
    invitation_code: str | None = None
    # SSO registration fields
    provider: str | None = Field(None, description="Provider type for SSO registration (feishu, dingtalk, etc.)")
    provider_code: str | None = Field(None, description="OAuth code for SSO registration")


class RegisterInitRequest(BaseModel):
    """Step 1: Initialize registration with account credentials."""
    username: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None
    target_tenant_id: uuid.UUID | None = None


class RegisterInitResponse(BaseModel):
    """Response after step 1 - user created, needs email verification."""
    user_id: uuid.UUID
    email: str
    access_token: str
    message: str = "Registration initiated. Please verify your email."
    user: "UserOut" # Include full user info
    needs_company_setup: bool = True
    target_tenant_id: uuid.UUID | None = None


class RegisterCompleteRequest(BaseModel):
    """Step 3: Complete registration after email verification."""
    token: str = Field(min_length=6, max_length=512, description="Email verification code")


class RegisterCompleteResponse(BaseModel):
    """Response after successful registration completion."""
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"
    needs_company_setup: bool = False


class SSORegisterRequest(BaseModel):
    """SSO registration - completely separate from normal registration."""
    provider: str = Field(description="Provider type (feishu, dingtalk, etc.)")
    code: str = Field(description="OAuth authorization code from provider")
    invitation_code: str | None = None


class UserLogin(BaseModel):
    login_identifier: str = Field(description="Email address for login")
    password: str
    tenant_id: uuid.UUID | None = None  # Optional: when set, restrict login to users of this tenant


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=6, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=6, max_length=512)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class NeedsVerificationResponse(BaseModel):
    """Response when user needs to verify email before continuing."""
    needs_verification: bool = True
    email: str
    message: str = "Email already registered but not verified. Please enter the verification code."


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"
    identity: "IdentityOut | None" = None
    needs_company_setup: bool = False
    tenant_name: str | None = None


class TenantChoice(BaseModel):
    """Multi-tenant login: tenant selection info."""
    tenant_id: uuid.UUID | None
    tenant_name: str
    tenant_slug: str
    logo_url: str | None = None


class MultiTenantResponse(BaseModel):
    """Response when multiple tenants match the same login identifier."""
    requires_tenant_selection: bool = True
    login_identifier: str
    tenants: list[TenantChoice]


class TenantSwitchRequest(BaseModel):
    tenant_id: uuid.UUID


class TenantSwitchResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    redirect_url: str | None = None
    message: str | None = None


class IdentityOut(BaseModel):
    """Global identity information."""
    id: uuid.UUID
    email: str | None = None
    phone: str | None = None
    username: str | None = None
    is_active: bool
    is_platform_admin: bool
    email_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: uuid.UUID
    identity_id: uuid.UUID | None = None
    username: str | None = None
    email: str | None = None
    display_name: str
    avatar_url: str | None = None
    role: str
    is_platform_admin: bool = False
    tenant_id: uuid.UUID | None = None
    title: str | None = None
    primary_mobile: str | None = None
    registration_source: str | None = None
    is_active: bool
    email_verified: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class IdentityProviderOut(BaseModel):
    id: uuid.UUID
    provider_type: str
    name: str
    is_active: bool
    sso_login_enabled: bool = False
    config: dict | None = None
    tenant_id: uuid.UUID | None = None
    updated_at: datetime | None = None
    created_at: datetime
    sso_domain: str | None = None

    model_config = {"from_attributes": True}


class OAuthAuthorizeResponse(BaseModel):
    authorization_url: str


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


class IdentityBindRequest(BaseModel):
    provider_type: str
    code: str  # OAuth code for binding


class IdentityUnbindRequest(BaseModel):
    provider_type: str


class UserUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    title: str | None = None
    primary_mobile: str | None = None


# ─── Agent ──────────────────────────────────────────────

class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100, description="Agent name, 2-100 characters")
    agent_type: str = "native"  # native | openclaw
    role_description: str = Field(default="", max_length=500, description="Role description, max 500 characters")
    bio: str | None = None
    welcome_message: str | None = None
    avatar_url: str | None = None
    # Soul
    personality: str = ""
    boundaries: str = ""
    # Model
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    # Permissions
    permission_scope_type: str = "company"  # company | user | custom
    permission_scope_ids: list[uuid.UUID] = []
    permission_access_level: str = "use"  # use | manage
    # Target tenant (admin-only override; otherwise ignored)
    tenant_id: uuid.UUID | None = None
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
    welcome_message: str | None = None
    status: str
    creator_id: uuid.UUID
    creator_username: str | None = None  # Populated by API layer; not in ORM model directly
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    autonomy_policy: dict
    tokens_used_today: int
    tokens_used_month: int
    tokens_used_total: int = 0
    cache_read_tokens_today: int = 0
    cache_read_tokens_month: int = 0
    cache_read_tokens_total: int = 0
    cache_creation_tokens_today: int = 0
    cache_creation_tokens_month: int = 0
    cache_creation_tokens_total: int = 0
    max_tokens_per_day: int | None = None
    max_tokens_per_month: int | None = None
    context_window_size: int = 100
    max_tool_rounds: int = 50
    max_triggers: int = 20
    min_poll_interval_min: int = 5
    webhook_rate_limit: int = 5
    heartbeat_enabled: bool = True
    heartbeat_interval_minutes: int = 240
    heartbeat_active_hours: str = "09:00-18:00"
    last_heartbeat_at: datetime | None = None
    timezone: str | None = None
    expires_at: datetime | None = None
    is_expired: bool = False
    is_system: bool = False
    access_mode: str = "company"
    company_access_level: str = "use"
    llm_calls_today: int = 0
    max_llm_calls_per_day: int = 1000
    agent_type: str = "native"
    openclaw_last_seen: datetime | None = None
    unread_count: int = 0
    has_api_key: bool = False
    api_key_hash: str | None = None
    # True when the current viewer already has an onboarding row for this
    # agent. Computed per-request by the API layer from the junction table;
    # not an ORM attribute, so callers must set it explicitly. Defaults to
    # True so list endpoints that don't care about onboarding don't leak
    # stale "needs onboarding" UI to users they shouldn't prompt.
    onboarded_for_me: bool = True
    created_at: datetime
    last_active_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentUpdate(BaseModel):
    name: str | None = None
    role_description: str | None = None
    bio: str | None = None
    welcome_message: str | None = None
    avatar_url: str | None = None
    autonomy_policy: dict | None = None
    primary_model_id: uuid.UUID | None = None
    fallback_model_id: uuid.UUID | None = None
    context_window_size: int | None = Field(default=None, ge=1, le=500)
    max_tokens_per_day: int | None = None
    max_tokens_per_month: int | None = None
    max_tool_rounds: int | None = None
    max_triggers: int | None = None
    min_poll_interval_min: int | None = None
    webhook_rate_limit: int | None = None
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


# ─── LLM ────────────────────────────────────────────────

class LLMModelCreate(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    label: str
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens_per_day: int | None = None
    enabled: bool = True
    supports_vision: bool = False
    max_output_tokens: int | None = None
    request_timeout: int | None = None

class LLMModelUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    label: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens_per_day: int | None = None
    enabled: bool | None = None
    supports_vision: bool | None = None
    max_output_tokens: int | None = None
    request_timeout: int | None = None


class LLMModelOut(BaseModel):
    id: uuid.UUID
    provider: str
    model: str
    base_url: str | None = None
    label: str
    temperature: float | None = None
    api_key_masked: str = ""
    max_tokens_per_day: int | None = None
    enabled: bool
    supports_vision: bool = False
    max_output_tokens: int | None = None
    request_timeout: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Channel Config ─────────────────────────────────────

class ChannelConfigCreate(BaseModel):
    channel_type: str = "feishu"
    app_id: str
    app_secret: str
    encrypt_key: str | None = None
    verification_token: str | None = None
    extra_config: dict | None = None


class ChannelConfigOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    channel_type: str
    app_id: str | None = None
    app_secret: str | None = None
    encrypt_key: str | None = None
    verification_token: str | None = None
    is_configured: bool
    is_connected: bool
    last_tested_at: datetime | None = None
    extra_config: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Approval ───────────────────────────────────────────

class ApprovalRequestOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None
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

class UserInviteRequest(BaseModel):
    emails: list[EmailStr] = Field(..., description="List of emails to invite")

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
    thinking: str | None = None
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


# ─── Gateway (OpenClaw) ─────────────────────────────────

class GatewayHistoryItem(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    sender_name: str | None = None
    created_at: datetime


class GatewayRelationshipItem(BaseModel):
    name: str
    type: str  # "human" or "agent"
    role: str | None = None  # e.g. "collaborator", "supervisor"
    description: str | None = None
    channels: list[str] = []  # e.g. ["feishu"], ["agent"]


class GatewayMessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: str | None = None
    sender_agent_name: str | None = None
    sender_user_name: str | None = None
    sender_user_id: str | None = None
    content: str
    created_at: datetime
    history: list[GatewayHistoryItem] = []



class GatewayPollResponse(BaseModel):
    messages: list[GatewayMessageOut] = []
    relationships: list[GatewayRelationshipItem] = []


class GatewayReportRequest(BaseModel):
    message_id: uuid.UUID
    result: str = Field(min_length=1)


class GatewaySendMessageRequest(BaseModel):
    target: str  # Name of target person or agent
    content: str = Field(min_length=1)
    channel: str | None = None  # Optional: "feishu", "agent", etc. Auto-detected if omitted.
