"""User and organization models."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """Platform user."""

    __tablename__ = "users"
    # Note: Unique constraints for (tenant_id, email) and (tenant_id, primary_mobile)
    # are handled via partial unique indexes in migration to allow NULL values

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(
        Enum("platform_admin", "org_admin", "agent_admin", "member", name="user_role_enum"),
        default="member",
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    title: Mapped[str | None] = mapped_column(String(100))

    # Generic identity fields for matching and SSO
    primary_mobile: Mapped[str | None] = mapped_column(String(50), index=True)
    registration_source: Mapped[str | None] = mapped_column(String(50), default="web")
    
    # Generic platform-stable identity IDs
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Legacy Feishu specific fields (Maintained for compatibility)
    feishu_user_id: Mapped[str | None] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Usage quotas (set by admin, defaults from tenant)
    quota_message_limit: Mapped[int] = mapped_column(Integer, default=50)
    quota_message_period: Mapped[str] = mapped_column(String(20), default="permanent")  # permanent|daily|weekly|monthly
    quota_messages_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_max_agents: Mapped[int] = mapped_column(Integer, default=2)
    quota_agent_ttl_hours: Mapped[int] = mapped_column(Integer, default=48)

    # Relationships
    created_agents: Mapped[list["Agent"]] = relationship(back_populates="creator", foreign_keys="Agent.creator_id")


# Forward reference for Agent used in User relationship
from app.models.agent import Agent  # noqa: E402, F401
from app.models.org import OrgMember  # noqa: E402, F401
