"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
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
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id", "reply_context_id"],
            ["channel_reply_contexts.tenant_id", "channel_reply_contexts.agent_id",
             "channel_reply_contexts.channel_configuration_id", "channel_reply_contexts.id"], ondelete="RESTRICT"),
        CheckConstraint("num_nonnulls(session_reply_id, group_reply_id) = 1", name="ck_channel_deliveries_one_reply"),
        CheckConstraint("attempt_count >= 0", name="ck_channel_deliveries_attempts"),
        CheckConstraint("delivery_status IN ('pending', 'delivered', 'failed', 'uncertain')", name="ck_channel_deliveries_status"),
        CheckConstraint("reply_operation IS NULL OR (reply_context_id IS NOT NULL AND reply_operation IN ('original', 'followup'))",
            name="ck_channel_deliveries_reply_operation"),
        Index("uq_channel_original_reply", "tenant_id", "reply_context_id", unique=True,
            postgresql_where=text("reply_operation = 'original'")),
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
    reply_context_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reply_operation: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ChannelReplyContextRecord(Base):
    __tablename__ = "channel_reply_contexts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.agent_id",
             "agent_channel_configurations.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "agent_id", "channel_configuration_id", "id"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "external_event_id"),
        CheckConstraint("context_version > 0", name="ck_channel_context_version"),
        CheckConstraint("(octet_length(nonce) = 0 AND octet_length(ciphertext) = 0) OR "
            "(octet_length(nonce) = 12 AND octet_length(ciphertext) BETWEEN 17 AND 65536)",
            name="ck_channel_context_cipher"),
        Index("ix_channel_context_expiry", "tenant_id", "expires_at", "id",
            postgresql_where=text("octet_length(ciphertext) > 0")),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    external_event_id: Mapped[str] = mapped_column(String(512))
    context_version: Mapped[int]
    key_version: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelActorLinkRecord(Base):
    __tablename__ = "channel_actor_links"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "external_actor_id"),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    external_actor_id: Mapped[str] = mapped_column(String(512))
    membership_id: Mapped[UUID]
    enabled: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelConversationRecord(Base):
    __tablename__ = "channel_conversations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.agent_id", "agent_channel_configurations.id"],
            ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id", "membership_id", "session_id"],
            ["sessions.tenant_id", "sessions.agent_id", "sessions.membership_id", "sessions.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "external_conversation_id", "membership_id"),
        CheckConstraint("message_cursor >= 0", name="ck_channel_conversation_cursor"),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    external_conversation_id: Mapped[str] = mapped_column(String(512))
    membership_id: Mapped[UUID]
    session_id: Mapped[UUID]
    message_cursor: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelInputRouteRecord(Base):
    __tablename__ = "channel_input_routes"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.agent_id", "agent_channel_configurations.id"],
            ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id", "session_input_id", "input_kind"],
            ["session_entries.tenant_id", "session_entries.agent_id", "session_entries.id", "session_entries.kind"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_input_id", "input_kind"],
            ["group_events.tenant_id", "group_events.id", "group_events.kind"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id", "reply_context_id"],
            ["channel_reply_contexts.tenant_id", "channel_reply_contexts.agent_id", "channel_reply_contexts.channel_configuration_id", "channel_reply_contexts.id"],
            ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "external_event_id"),
        CheckConstraint("num_nonnulls(session_input_id, group_input_id) = 1", name="ck_channel_input_route_source"),
        Index("ix_channel_route_session_input", "tenant_id", "agent_id", "session_input_id"),
        Index("ix_channel_route_group_input", "tenant_id", "agent_id", "group_input_id"),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    external_event_id: Mapped[str] = mapped_column(String(512))
    session_input_id: Mapped[UUID | None]
    group_input_id: Mapped[UUID | None]
    input_kind: Mapped[str] = mapped_column(String(16), Computed("'input'", persisted=True))
    destination: Mapped[str] = mapped_column(String(512))
    reply_context_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelSyncCursorRecord(Base):
    __tablename__ = "channel_sync_cursors"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "agent_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.agent_id",
             "agent_channel_configurations.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "stream_key"),
        CheckConstraint("cursor_version > 0", name="ck_channel_sync_cursor_version"),
        CheckConstraint("coordinate_kind IN ('token', 'cursor', 'done')", name="ck_channel_sync_cursor_kind"),
        CheckConstraint("octet_length(nonce) = 12 AND octet_length(ciphertext) BETWEEN 17 AND 65536",
            name="ck_channel_sync_cursor_cipher"),
        Index("ix_channel_sync_pending", "id",
            postgresql_where=text("coordinate_kind <> 'done'")),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    stream_key: Mapped[str] = mapped_column(String(512))
    coordinate_kind: Mapped[str] = mapped_column(String(16))
    external_event_id: Mapped[str] = mapped_column(String(512))
    cursor_version: Mapped[int]
    key_version: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelGroupLinkRecord(Base):
    __tablename__ = "channel_group_links"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "channel_configuration_id"],
            ["agent_channel_configurations.tenant_id", "agent_channel_configurations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "group_id"], ["groups.tenant_id", "groups.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "channel_configuration_id", "external_group_id"),
        CheckConstraint("message_cursor >= 0", name="ck_channel_group_cursor"),
        {"info": {"owner": "channel"}},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    channel_configuration_id: Mapped[UUID]
    external_group_id: Mapped[str] = mapped_column(String(512))
    group_id: Mapped[UUID]
    message_cursor: Mapped[int] = mapped_column(default=0, server_default="0")
    enabled: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
