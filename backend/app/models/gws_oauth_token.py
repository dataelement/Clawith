"""Google Workspace OAuth token model — per-agent, per-user encrypted token storage."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GwsOAuthToken(Base):
    """Stores encrypted Google OAuth tokens per (agent, user) pair.

    Each user interacting with an agent gets their own OAuth session,
    enabling multi-user concurrent access without token conflicts.

    Fields access_token and refresh_token are encrypted at rest
    using AES-256-CBC via encrypt_data/decrypt_data from app.core.security.
    """

    __tablename__ = "gws_oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=True,
        index=True,
    )

    # Google user info
    google_email: Mapped[str] = mapped_column(Text, nullable=False)
    google_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # OAuth tokens (encrypted via encrypt_data/decrypt_data)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    # State
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "user_id", name="uq_gws_oauth_agent_user"),
    )
