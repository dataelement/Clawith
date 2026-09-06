"""Private local-login persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class LoginVerifierRecord(Base):
    __tablename__ = "login_verifiers"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_login_verifiers_account"),
        UniqueConstraint("login_name", name="uq_login_verifiers_login_name"),
        CheckConstraint("kdf_version > 0", name="ck_login_verifiers_kdf_version"),
        {"info": {"owner": "auth"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    login_name: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    kdf_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kdf_version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoginSessionRecord(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_login_sessions_tenant_id_id"),
        UniqueConstraint("token_hash", name="uq_login_sessions_token_hash"),
        CheckConstraint("authorization_schema_version > 0", name="ck_login_sessions_authorization_version"),
        CheckConstraint("expires_at > created_at", name="ck_login_sessions_expiry"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "membership_id"],
            ["memberships.tenant_id", "memberships.account_id", "memberships.id"],
            ondelete="RESTRICT",
        ),
        {"info": {"owner": "auth"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    membership_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    frozen_authorization: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    authorization_schema_version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    logged_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
