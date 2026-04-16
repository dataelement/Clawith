"""Tenant (Company) model — multi-tenancy isolation boundary."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tenant(Base):
    """A company/organization that uses the platform."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    im_provider: Mapped[str] = mapped_column(
        Enum("feishu", "dingtalk", "wecom", "microsoft_teams", "web_only", name="im_provider_enum"),
        default="web_only",
        nullable=False,
    )
    im_config: Mapped[dict | None] = mapped_column(JSON, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Default quotas for new users
    default_message_limit: Mapped[int] = mapped_column(Integer, default=50)
    default_message_period: Mapped[str] = mapped_column(String(20), default="permanent")
    default_max_agents: Mapped[int] = mapped_column(Integer, default=2)
    default_agent_ttl_hours: Mapped[int] = mapped_column(Integer, default=48)
    default_max_llm_calls_per_day: Mapped[int] = mapped_column(Integer, default=100)

    # Heartbeat frequency floor (minutes) — agents cannot heartbeat faster than this
    min_heartbeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=240)

    # Default timezone for all agents in this company (IANA format, e.g. "Asia/Shanghai")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # SSO configuration
    sso_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sso_domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)

    # Trigger limits — defaults for new agents & floor values
    default_max_triggers: Mapped[int] = mapped_column(Integer, default=20)
    min_poll_interval_floor: Mapped[int] = mapped_column(Integer, default=5)
    max_webhook_rate_ceiling: Mapped[int] = mapped_column(Integer, default=5)

    # A2A async communication (notify / task_delegate)
    # When False, all agent-to-agent messages use synchronous consult mode
    a2a_async_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

