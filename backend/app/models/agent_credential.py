"""Agent credential model for storing login credentials and cookies.

Each AgentCredential stores encrypted login credentials (username/password)
and browser cookies for a specific platform, enabling automatic login
state injection when creating new AgentBay browser sessions.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentCredential(Base):
    """Stores encrypted credentials and cookies for an agent on a specific platform.

    The cookies_json field holds an encrypted JSON array of Playwright-compatible
    cookie objects. The username and password fields are also encrypted using
    the platform's AES-256-CBC encrypt_data/decrypt_data utilities.

    Lifecycle:
    - Created manually by admin via UI (Phase 2)
    - Updated automatically after successful Take Control login (Phase 3)
    - Cookies injected into new browser sessions via CDP (Phase 2)
    """

    __tablename__ = "agent_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Identity fields
    credential_type: Mapped[str] = mapped_column(
        String(20), default="website"
    )  # website | email | social | api_key
    platform: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g. "baidu.com", "gmail.com"
    display_name: Mapped[str] = mapped_column(
        String(200), default=""
    )  # human-readable label

    # Manual credentials (encrypted, used for auto-login in Take Control)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    password: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    login_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # login page URL for Re-login

    # Auto-managed cookie state
    cookies_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # encrypted JSON array of Playwright cookies
    cookies_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # when cookies were last captured/updated

    # Runtime state
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # active | expired | needs_relogin
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # last successful login time
    last_injected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # last injection into a browser session

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
