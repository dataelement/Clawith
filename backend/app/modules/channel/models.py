"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AgentChannelConfigurationRecord(Base):
    __tablename__ = "agent_channel_configurations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "agent_id", "id"),
        UniqueConstraint("tenant_id", "provider", "external_identity"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "credential_owner_kind", "credential_owner_id"],
            ["credentials.tenant_id", "credentials.id", "credentials.owner_kind", "credentials.owner_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("configuration_version > 0", name="ck_agent_channel_configurations_version"),
        CheckConstraint(
            "num_nonnulls(credential_id, credential_owner_kind, credential_owner_id) IN (0, 3)",
            name="ck_agent_channel_configurations_credential",
        ),
        CheckConstraint(
            "credential_id IS NULL OR (credential_owner_kind = 'tenant' AND credential_owner_id = tenant_id) OR (credential_owner_kind = 'agent' AND credential_owner_id = agent_id)",
            name="ck_agent_channel_configurations_owner",
        ),
        {"info": {"owner": "channel"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    provider: Mapped[str] = mapped_column(String(64))
    external_identity: Mapped[str] = mapped_column(String(512))
    configuration_version: Mapped[int]
    non_secret_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool]
    credential_id: Mapped[UUID | None]
    credential_owner_kind: Mapped[str | None] = mapped_column(String(16))
    credential_owner_id: Mapped[UUID | None]


class ChannelDeliveryRecord(Base):
    __tablename__ = "channel_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "channel_configuration_id"],
            [
                "agent_channel_configurations.tenant_id",
                "agent_channel_configurations.agent_id",
                "agent_channel_configurations.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "session_reply_id", "reply_kind"],
            ["session_entries.tenant_id", "session_entries.agent_id", "session_entries.id", "session_entries.kind"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "group_reply_id", "reply_kind"],
            ["group_events.tenant_id", "group_events.agent_id", "group_events.id", "group_events.kind"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "channel_configuration_id", "delivery_key"),
        CheckConstraint("num_nonnulls(session_reply_id, group_reply_id) = 1", name="ck_channel_deliveries_one_reply"),
        CheckConstraint("attempt_count >= 0", name="ck_channel_deliveries_attempts"),
        CheckConstraint("delivery_status IN ('pending', 'delivered', 'failed')", name="ck_channel_deliveries_status"),
        {"info": {"owner": "channel"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    session_reply_id: Mapped[UUID | None]
    group_reply_id: Mapped[UUID | None]
    reply_kind: Mapped[str] = mapped_column(String(16), Computed("'reply'", persisted=True))
    destination: Mapped[str] = mapped_column(String(512))
    delivery_key: Mapped[str] = mapped_column(String(512))
    attempt_count: Mapped[int]
    delivery_status: Mapped[str] = mapped_column(String(16))
    provider_acknowledgement: Mapped[str | None] = mapped_column(String(512))
    last_error: Mapped[str | None] = mapped_column(String(512))
