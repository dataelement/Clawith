"""Virtual organization models for agent-centric org structure."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    event,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session as OrmSession, mapped_column, relationship

from app.database import Base


class VirtualDepartment(Base):
    """Virtual department tree used for the agent organization view."""

    __tablename__ = "virtual_departments"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_virtual_departments_tenant_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_departments.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    org_level: Mapped[str] = mapped_column(String(30), default="department", nullable=False)
    is_core: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped["VirtualDepartment | None"] = relationship(
        "VirtualDepartment", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["VirtualDepartment"]] = relationship("VirtualDepartment", back_populates="parent")


class AgentVirtualOrg(Base):
    """Agent assignment within the virtual organization."""

    __tablename__ = "agent_virtual_org"
    __table_args__ = (
        CheckConstraint("level IN ('L1', 'L2', 'L3', 'L4', 'L5')", name="ck_agent_virtual_org_level"),
        CheckConstraint("org_bucket IN ('core', 'expert')", name="ck_agent_virtual_org_org_bucket"),
        Index(
            "uq_agent_virtual_org_primary_per_agent",
            "agent_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
            postgresql_where=text("is_primary"),
        ),
        Index(
            "uq_agent_virtual_org_primary_instance_per_template",
            "tenant_id",
            "template_id",
            unique=True,
            sqlite_where=text("is_org_primary_instance = 1 AND template_id IS NOT NULL"),
            postgresql_where=text("is_org_primary_instance AND template_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_templates.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="L3", nullable=False)
    org_bucket: Mapped[str] = mapped_column(String(20), default="core", nullable=False)
    manager_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_org_primary_instance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    department: Mapped["VirtualDepartment"] = relationship("VirtualDepartment")


class AgentVirtualTag(Base):
    """Agent tags used for filtering in the virtual org views."""

    __tablename__ = "agent_virtual_tags"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "tag", name="uq_agent_virtual_tags_tenant_agent_tag"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _validate_agent_virtual_org_tenant_consistency(session: OrmSession, assignment: AgentVirtualOrg) -> None:
    agent = session.get(Agent, assignment.agent_id)
    department = session.get(VirtualDepartment, assignment.department_id)

    if agent is not None and agent.tenant_id != assignment.tenant_id:
        raise ValueError("agent_virtual_org tenant mismatch with agent tenant")

    if department is not None and department.tenant_id != assignment.tenant_id:
        raise ValueError("agent_virtual_org tenant mismatch with department tenant")

    if agent is not None and department is not None and agent.tenant_id != department.tenant_id:
        raise ValueError("agent_virtual_org links agent and department from different tenants")

    if agent is not None and assignment.template_id != agent.template_id:
        raise ValueError("agent_virtual_org template_id must match the linked agent template")

    if assignment.manager_agent_id is not None:
        manager = session.get(Agent, assignment.manager_agent_id)
        if manager is not None and manager.tenant_id != assignment.tenant_id:
            raise ValueError("agent_virtual_org manager tenant mismatch")


def _validate_agent_virtual_tag_tenant_consistency(session: OrmSession, tag: AgentVirtualTag) -> None:
    agent = session.get(Agent, tag.agent_id)
    if agent is not None and agent.tenant_id != tag.tenant_id:
        raise ValueError("agent_virtual_tags tenant mismatch with agent tenant")


def _validate_virtual_department_tenant_consistency(session: OrmSession, department: VirtualDepartment) -> None:
    if department.parent_id is None:
        return

    parent = session.get(VirtualDepartment, department.parent_id)
    if parent is not None and parent.tenant_id != department.tenant_id:
        raise ValueError("virtual_departments parent tenant mismatch")


@event.listens_for(OrmSession, "before_flush")
def validate_virtual_org_assignments(session: OrmSession, flush_context, instances) -> None:
    for obj in session.new.union(session.dirty):
        if isinstance(obj, VirtualDepartment):
            _validate_virtual_department_tenant_consistency(session, obj)
        elif isinstance(obj, AgentVirtualOrg):
            _validate_agent_virtual_org_tenant_consistency(session, obj)
        elif isinstance(obj, AgentVirtualTag):
            _validate_agent_virtual_tag_tenant_consistency(session, obj)


from app.models.agent import Agent, AgentTemplate  # noqa: E402, F401
from app.models.tenant import Tenant  # noqa: E402, F401
