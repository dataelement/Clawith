"""Global Skill registry model."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Skill(Base):
    """A globally registered skill definition."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="general")
    icon: Mapped[str] = mapped_column(String(10), default="📋")
    folder_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Related files (SKILL.md + optional auxiliaries)
    files: Mapped[list["SkillFile"]] = relationship(back_populates="skill", cascade="all, delete-orphan")


class SkillFile(Base):
    """A file within a skill folder (e.g. SKILL.md, scripts/helper.py)."""

    __tablename__ = "skill_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)  # e.g. "SKILL.md" or "scripts/helper.py"
    content: Mapped[str] = mapped_column(Text, default="")

    skill: Mapped["Skill"] = relationship(back_populates="files")
