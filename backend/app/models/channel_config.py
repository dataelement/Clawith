"""Channel configuration models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChannelConfig(Base):
    """Channel configuration for a digital employee (e.g. Feishu bot credentials)."""

    __tablename__ = "channel_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(
        Enum("feishu", "wecom", "wechat", "whatsapp", "dingtalk", "slack", "discord","atlassian", "microsoft_teams", "agentbay", name="channel_type_enum"),
        default="feishu",
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("agent_id", "channel_type", name="uq_channel_configs_agent_channel"),)

    # Feishu specific config
    app_id: Mapped[str | None] = mapped_column(String(255))
    app_secret: Mapped[str | None] = mapped_column(String(512))
    encrypt_key: Mapped[str | None] = mapped_column(String(255))
    verification_token: Mapped[str | None] = mapped_column(String(255))

    # Status
    is_configured: Mapped[bool] = mapped_column(default=False)
    is_connected: Mapped[bool] = mapped_column(default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Additional config as JSON for extensibility
    extra_config: Mapped[dict] = mapped_column(JSON, default={})

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    agent: Mapped["Agent"] = relationship(back_populates="channel_config")


from app.models.agent import Agent  # noqa: E402, F401
