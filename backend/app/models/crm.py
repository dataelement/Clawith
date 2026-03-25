"""CRM models — lightweight CRM for trade contacts and deals."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CRMContact(Base):
    __tablename__ = "crm_contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(100))  # google_maps, apollo, manual, chatwoot
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    chatwoot_contact_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    deals: Mapped[list["CRMDeal"]] = relationship(back_populates="contact", cascade="all, delete-orphan")
    activities: Mapped[list["CRMActivity"]] = relationship(back_populates="contact", cascade="all, delete-orphan")


class CRMDeal(Base):
    __tablename__ = "crm_deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_contacts.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), default="lead", nullable=False)  # lead/contacted/qualified/proposal/negotiation/won/lost
    value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    contact: Mapped["CRMContact"] = relationship(back_populates="deals")


class CRMActivity(Base):
    __tablename__ = "crm_activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_contacts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # email/call/meeting/note/deal_update
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    contact: Mapped["CRMContact"] = relationship(back_populates="activities")
