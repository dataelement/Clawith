"""Digital Employee (Agent) models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Default context window size — used as the fallback when
# agent.context_window_size is None or 0 across all channels.
# Centralizing this constant prevents inconsistent fallback values
# (see: https://github.com/dataelement/Clawith/issues/238).
DEFAULT_CONTEXT_WINDOW_SIZE = 100


class Agent(Base):
    """Digital employee (Agent) instance.

    agent_type: 'native' (platform-hosted) or 'openclaw' (remote OpenClaw bot).
    """

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role_description: Mapped[str] = mapped_column(String(500), default="")
    bio: Mapped[str | None] = mapped_column(Text)
    welcome_message: Mapped[str | None] = mapped_column(Text, default=None)

    # Ownership
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))

    # Agent type: 'native' (platform-hosted LLM) or 'openclaw' (remote OpenClaw bot)
    agent_type: Mapped[str] = mapped_column(String(20), default="native", nullable=False)
    # API key hash for OpenClaw gateway authentication
    api_key_hash: Mapped[str | None] = mapped_column(String(128))
    # Last time OpenClaw polled the gateway (online status indicator)
    openclaw_last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Runtime
    status: Mapped[str] = mapped_column(
        Enum("creating", "running", "idle", "stopped", "error", name="agent_status_enum", create_constraint=False),
        default="creating",
        nullable=False,
    )
    container_id: Mapped[str | None] = mapped_column(String(100))
    container_port: Mapped[int | None] = mapped_column(Integer)

    # LLM config
    primary_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_models.id"))
    fallback_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_models.id"))

    # Autonomy policy (L1/L2/L3)
    autonomy_policy: Mapped[dict] = mapped_column(
        JSON,
        default={
            "read_files": "L1",
            "write_workspace_files": "L2",
            "send_feishu_message": "L2",
            "send_external_message": "L3",
            "modify_soul": "L3",
            "access_business_system_read": "L2",
            "access_business_system_write": "L3",
            "delete_files": "L3",
            "create_calendar_event": "L2",
            "financial_operations": "L3",
        },
    )

    # Token usage control
    max_tokens_per_day: Mapped[int | None] = mapped_column(Integer)
    max_tokens_per_month: Mapped[int | None] = mapped_column(Integer)
    tokens_used_today: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used_month: Mapped[int] = mapped_column(Integer, default=0)
    last_daily_reset: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_monthly_reset: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tokens_used_total: Mapped[int] = mapped_column(Integer, default=0)
    context_window_size: Mapped[int] = mapped_column(Integer, default=100)
    max_tool_rounds: Mapped[int] = mapped_column(Integer, default=50)

    # Trigger limits (per-agent, configurable from Settings UI)
    max_triggers: Mapped[int] = mapped_column(Integer, default=20)
    min_poll_interval_min: Mapped[int] = mapped_column(Integer, default=5)
    webhook_rate_limit: Mapped[int] = mapped_column(Integer, default=5)

    # Expiry control
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False)

    # System agent flag — system agents (e.g. OKR Agent) cannot be deleted by users
    # and their system triggers are protected from user deletion.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Daily LLM call limit
    llm_calls_today: Mapped[int] = mapped_column(Integer, default=0)
    max_llm_calls_per_day: Mapped[int] = mapped_column(Integer, default=100)
    llm_calls_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Template
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_templates.id"))

    # Heartbeat (proactive agent awareness)
    heartbeat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    heartbeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=240)
    heartbeat_active_hours: Mapped[str] = mapped_column(String(20), default="09:00-18:00")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timezone (IANA format, e.g. "Asia/Shanghai"). None = inherit from tenant.
    timezone: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    creator: Mapped["User"] = relationship("User", back_populates="created_agents", foreign_keys=[creator_id])

    @property
    def has_api_key(self) -> bool:
        """Whether this agent has an API key configured."""
        return bool(self.api_key_hash)
    permissions: Mapped[list["AgentPermission"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    channel_config: Mapped["ChannelConfig | None"] = relationship(back_populates="agent", uselist=False)
    primary_model: Mapped["LLMModel | None"] = relationship(foreign_keys=[primary_model_id])
    fallback_model: Mapped["LLMModel | None"] = relationship(foreign_keys=[fallback_model_id])


class AgentPermission(Base):
    """Access permission for a digital employee."""

    __tablename__ = "agent_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(
        Enum("company", "department", "user", name="permission_scope_enum"),
        nullable=False,
    )
    # scope_id: null for company, user_id for user scope
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # access_level: 'use' = task/chat/tool/skill/workspace only, 'manage' = full access
    access_level: Mapped[str] = mapped_column(String(20), default="use", nullable=False)

    agent: Mapped["Agent"] = relationship(back_populates="permissions")


class AgentTemplate(Base):
    """Digital employee template for quick creation."""

    __tablename__ = "agent_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(50), default="🤖")
    category: Mapped[str] = mapped_column(String(50), default="general")
    soul_template: Mapped[str] = mapped_column(Text, default="")
    default_skills: Mapped[list] = mapped_column(JSON, default=[])
    default_autonomy_policy: Mapped[dict] = mapped_column(JSON, default={})
    is_builtin: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Import for relationship resolution
from app.models.task import Task  # noqa: E402, F401
from app.models.channel_config import ChannelConfig  # noqa: E402, F401
from app.models.user import User  # noqa: E402, F401
from app.models.llm import LLMModel  # noqa: E402, F401
