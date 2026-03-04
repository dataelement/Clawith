"""User and organization models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """Platform user."""

    __tablename__ = "users"

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
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    title: Mapped[str | None] = mapped_column(String(100))

    # Feishu SSO
    feishu_open_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    feishu_union_id: Mapped[str | None] = mapped_column(String(255))
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
    department: Mapped["Department | None"] = relationship(back_populates="members", foreign_keys=[department_id])
    created_agents: Mapped[list["Agent"]] = relationship(back_populates="creator", foreign_keys="Agent.creator_id")


class Department(Base):
    """Organization department (tree structure)."""

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    parent: Mapped["Department | None"] = relationship(
        "Department", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Department"]] = relationship("Department", back_populates="parent")
    manager: Mapped["User | None"] = relationship("User", foreign_keys=[manager_id])
    members: Mapped[list["User"]] = relationship(
        "User", back_populates="department", foreign_keys="User.department_id"
    )


# Forward reference for Agent used in User relationship
from app.models.agent import Agent  # noqa: E402, F401
