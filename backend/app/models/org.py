"""Organization structure models cached from external directory providers."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.tenant import Tenant  # noqa: F401


class OrgDepartment(Base):
    """Department synced from an external directory provider."""

    __tablename__ = "org_departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feishu_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    wecom_id: Mapped[str | None] = mapped_column(String(100), index=True)
    sync_provider: Mapped[str] = mapped_column(String(20), default="feishu", server_default="feishu", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("org_departments.id"))
    path: Mapped[str] = mapped_column(String(500), default="")
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["OrgMember"]] = relationship(back_populates="department")


class OrgMember(Base):
    """Person synced from an external directory provider."""

    __tablename__ = "org_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feishu_open_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    feishu_user_id: Mapped[str | None] = mapped_column(String(100))
    wecom_user_id: Mapped[str | None] = mapped_column(String(100), index=True)
    sync_provider: Mapped[str] = mapped_column(String(20), default="feishu", server_default="feishu", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_translit_full: Mapped[str | None] = mapped_column(String(255), index=True)
    name_translit_initial: Mapped[str | None] = mapped_column(String(50), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(200), default="")
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("org_departments.id"))
    department_path: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="active")
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    department: Mapped["OrgDepartment | None"] = relationship(back_populates="members")


class AgentRelationship(Base):
    """Relationship between an agent and an org member."""

    __tablename__ = "agent_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("org_members.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False, default="collaborator")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    member: Mapped["OrgMember"] = relationship()


class AgentAgentRelationship(Base):
    """Relationship between two agents (digital employees)."""

    __tablename__ = "agent_agent_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    target_agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False, default="collaborator")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    target_agent = relationship("Agent", foreign_keys=[target_agent_id])
