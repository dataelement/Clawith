"""Private Identity and Tenant persistence models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AccountRecord(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "platform_role IS NULL OR platform_role = 'platform_admin'",
            name="ck_accounts_platform_role",
        ),
        {"info": {"owner": "identity_tenant"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    platform_role: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantRecord(Base):
    __tablename__ = "tenants"
    __table_args__ = ({"info": {"owner": "identity_tenant"}},)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MembershipRecord(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id_id"),
        UniqueConstraint("tenant_id", "account_id", name="uq_memberships_tenant_account"),
        UniqueConstraint(
            "tenant_id", "account_id", "id", name="uq_memberships_tenant_account_id"
        ),
        CheckConstraint("role IN ('tenant_admin', 'member')", name="ck_memberships_role"),
        {"info": {"owner": "identity_tenant"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
