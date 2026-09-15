"""Owner-private S2 persistence records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ToolDefinitionRecord(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "name"),
        UniqueConstraint("tenant_id", "id", "catalog_item_id"),
        UniqueConstraint("tenant_id", "id", "source"),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_item_id"],
            ["capability_catalog_items.tenant_id", "capability_catalog_items.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("source IN ('builtin', 'product', 'mcp', 'external')", name="ck_tool_definitions_source"),
        CheckConstraint("name ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_tool_definitions_name"),
        CheckConstraint("schema_version > 0 AND configuration_version > 0", name="ck_tool_definitions_versions"),
        CheckConstraint(
            "source <> 'mcp' OR (catalog_item_id IS NOT NULL AND upstream_name IS NOT NULL)",
            name="ck_tool_definitions_mcp_source",
        ),
        {"info": {"owner": "tool"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    catalog_item_id: Mapped[UUID | None]
    source: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    upstream_name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(String(16384))
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[int]
    executor_key: Mapped[str] = mapped_column(String(128))
    configuration_version: Mapped[int]
    non_secret_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool]


class AgentMCPConnectionRecord(Base):
    __tablename__ = "agent_mcp_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_item_id"],
            ["capability_catalog_items.tenant_id", "capability_catalog_items.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "agent_id", "catalog_item_id"),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_item_id", "catalog_kind"],
            ["capability_catalog_items.tenant_id", "capability_catalog_items.id", "capability_catalog_items.kind"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "agent_id", "catalog_item_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "credential_owner_kind", "credential_owner_id"],
            ["credentials.tenant_id", "credentials.id", "credentials.owner_kind", "credentials.owner_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "num_nonnulls(credential_id, credential_owner_kind, credential_owner_id) IN (0, 3)",
            name="ck_mcp_connections_credential_complete",
        ),
        CheckConstraint(
            "credential_id IS NULL OR (credential_owner_kind = 'agent' AND credential_owner_id = agent_id)",
            name="ck_mcp_connections_credential_owner",
        ),
        CheckConstraint("configuration_version > 0 AND discovery_version > 0", name="ck_mcp_connections_versions"),
        {"info": {"owner": "tool"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    catalog_item_id: Mapped[UUID]
    catalog_kind: Mapped[str] = mapped_column(String(16), Computed("'mcp'", persisted=True))
    credential_id: Mapped[UUID | None]
    credential_owner_kind: Mapped[str | None] = mapped_column(String(16))
    credential_owner_id: Mapped[UUID | None]
    auth_required: Mapped[bool]
    configuration_version: Mapped[int]
    non_secret_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool]
    discovery_version: Mapped[int]
    discovered_tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class AgentToolGrantRecord(Base):
    __tablename__ = "agent_tool_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "agent_id", "tool_definition_id"),
        ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id", "tool_source"],
            ["tool_definitions.tenant_id", "tool_definitions.id", "tool_definitions.source"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id", "catalog_item_id"],
            ["tool_definitions.tenant_id", "tool_definitions.id", "tool_definitions.catalog_item_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "catalog_item_id", "mcp_connection_id"],
            [
                "agent_mcp_connections.tenant_id",
                "agent_mcp_connections.agent_id",
                "agent_mcp_connections.catalog_item_id",
                "agent_mcp_connections.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by_membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "credential_owner_kind", "credential_owner_id"],
            ["credentials.tenant_id", "credentials.id", "credentials.owner_kind", "credentials.owner_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "num_nonnulls(credential_id, credential_owner_kind, credential_owner_id) IN (0, 3)",
            name="ck_grants_credential_complete",
        ),
        CheckConstraint(
            "(tool_source = 'mcp' AND mcp_connection_id IS NOT NULL AND catalog_item_id IS NOT NULL AND credential_id IS NULL) OR (tool_source <> 'mcp' AND mcp_connection_id IS NULL)",
            name="ck_agent_tool_grants_mcp_shape",
        ),
        CheckConstraint(
            "credential_id IS NULL OR (credential_owner_kind = 'tenant' AND credential_owner_id = tenant_id) OR (credential_owner_kind = 'agent' AND credential_owner_id = agent_id)",
            name="ck_agent_tool_grants_credential_owner",
        ),
        CheckConstraint("configuration_version > 0", name="ck_agent_tool_grants_version"),
        {"info": {"owner": "tool"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[UUID]
    tool_definition_id: Mapped[UUID]
    tool_source: Mapped[str] = mapped_column(String(16))
    catalog_item_id: Mapped[UUID | None]
    mcp_connection_id: Mapped[UUID | None]
    credential_id: Mapped[UUID | None]
    credential_owner_kind: Mapped[str | None] = mapped_column(String(16))
    credential_owner_id: Mapped[UUID | None]
    configuration_version: Mapped[int]
    non_secret_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    granted_by_membership_id: Mapped[UUID | None]
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MembershipAgentToolConnectionRecord(Base):
    __tablename__ = "membership_agent_tool_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "agent_id"], ["agents.tenant_id", "agents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"], ["memberships.tenant_id", "memberships.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id"],
            ["tool_definitions.tenant_id", "tool_definitions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "credential_owner_kind", "credential_owner_id"],
            ["credentials.tenant_id", "credentials.id", "credentials.owner_kind", "credentials.owner_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "membership_id", "agent_id", "tool_definition_id", "credential_id"),
        CheckConstraint(
            "credential_owner_kind = 'membership' AND credential_owner_id = membership_id",
            name="ck_membership_tool_connections_owner",
        ),
        CheckConstraint(
            "configuration_version > 0 AND discovery_version > 0", name="ck_membership_tool_connections_versions"
        ),
        {"info": {"owner": "tool"}},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    membership_id: Mapped[UUID]
    agent_id: Mapped[UUID]
    tool_definition_id: Mapped[UUID]
    credential_id: Mapped[UUID]
    credential_owner_kind: Mapped[str] = mapped_column(String(16))
    credential_owner_id: Mapped[UUID]
    label: Mapped[str] = mapped_column(String(200))
    configuration_version: Mapped[int]
    non_secret_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool]
    discovery_version: Mapped[int]
    discovered_tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
