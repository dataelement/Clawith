"""User and organization models."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.associationproxy import association_proxy

from app.database import Base



class Identity(Base):
    """
    Physical Identity (Lark ID).
    Represents a natural person globally across all tenants.
    """

    __tablename__ = "identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Global unique identifiers for login
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    
    # Global authentication
    password_hash: Mapped[str | None] = mapped_column(String(255))
    
    # Global status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Verification status
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant_users: Mapped[list["User"]] = relationship(back_populates="identity")


class User(Base):
    """
    Tenant Identity (Member ID).
    Represents a person's role and profile within a specific company.
    """

    __tablename__ = "users"
    # Note: Unique constraints for (tenant_id, username), (tenant_id, email) and (tenant_id, primary_mobile)
    # are handled via partial unique indexes in migration to allow NULL values

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Link to global identity
    identity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identities.id"), index=True)

    # Tenant context
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))

    # Tenant-specific profile
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    title: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        Enum("platform_admin", "org_admin", "agent_admin", "member", name="user_role_enum"),
        default="member",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    registration_source: Mapped[str | None] = mapped_column(String(50), default="web")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Usage quotas (set by admin, defaults from tenant)
    quota_message_limit: Mapped[int] = mapped_column(Integer, default=50)
    quota_message_period: Mapped[str] = mapped_column(String(20), default="permanent")  # permanent|daily|weekly|monthly
    quota_messages_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_max_agents: Mapped[int] = mapped_column(Integer, default=2)
    quota_agent_ttl_hours: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    # lazy="selectin" is required because association_proxy fields (email, username,
    # password_hash, email_verified, primary_mobile) delegate to this relationship.
    # Without eager loading, any proxy access in an async context triggers a synchronous
    # IO call inside a greenlet, raising sqlalchemy.exc.MissingGreenlet.
    identity: Mapped["Identity"] = relationship(back_populates="tenant_users", lazy="selectin")

    # Association proxies for backward compatibility
    email = association_proxy("identity", "email")
    username = association_proxy("identity", "username")
    password_hash = association_proxy("identity", "password_hash")
    email_verified = association_proxy("identity", "email_verified")
    primary_mobile = association_proxy("identity", "phone")

    created_agents: Mapped[list["Agent"]] = relationship(back_populates="creator", foreign_keys="Agent.creator_id")


# Forward reference for Agent used in User relationship
from app.models.agent import Agent  # noqa: E402, F401
from app.models.org import OrgMember  # noqa: E402, F401
