"""Agent Bundle models — multi-agent team templates.

A Bundle packages N agent definitions + their A2A relationship graph + R MCP
server attachments into a single hireable unit. Bundles are dev-shipped via
folders at ``backend/agent_bundles/<slug>/`` (see ``bundle_seeder``) and exposed
in the Talent Market alongside single-agent templates.

When a tenant "hires" a bundle (POST /api/bundles/{slug}/hire), the platform
transactionally:
  1. Creates one Agent per AgentBundleAgent row (verbatim — no rename / no subset).
  2. Registers each AgentBundleMcpServer for the tenant and binds it to every
     agent whose ``default_mcp_attach`` references that server's ``local_key``.
  3. Creates AgentAgentRelationship rows per AgentBundleRelationship, mapping
     bundle-local slugs to the freshly-created agent IDs.

Bundles are global (no tenant_id) like AgentTemplate. Tenant scoping happens at
hire-time on the Agent / Tool / AgentAgentRelationship rows that get created.

Schema is purely additive — existing AgentTemplate / agents / tools flows are
unchanged.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentBundle(Base):
    """A team template — N agents + K relationships + R MCP servers, hireable as one."""

    __tablename__ = "agent_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # English-language counterparts for bilingual bundles. Optional — when
    # absent, the frontend falls back to the primary (Chinese) fields so
    # zh-only authors keep working without change.
    name_en: Mapped[str | None] = mapped_column(String(200), default=None, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    # Short text code shown on the bundle card, e.g. "AU8". Mirror AgentTemplate.icon
    # convention; no emoji per project style.
    icon: Mapped[str] = mapped_column(String(50), default="TM")
    category: Mapped[str] = mapped_column(String(50), default="general")
    # 2-4 short bullets summarising what the team delivers, shown on the card
    capability_bullets: Mapped[list] = mapped_column(JSON, default=list)
    capability_bullets_en: Mapped[list | None] = mapped_column(JSON, default=None, nullable=True)
    # Bundle authoring version — bumped by the author when bundle contents change.
    # Seeder uses (slug, is_builtin) for upsert key, version is informational.
    version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    # Author-declared natural language of the bundle's agent content (soul.md,
    # skill files, agent names). The Talent Market filters by current UI locale
    # so EN users see only EN-native bundles and CN users only CN-native ones —
    # we do NOT mix-and-match a single localized name onto an opposite-language
    # soul. Values: "zh" (default) or "en".
    language: Mapped[str] = mapped_column(String(8), default="zh", nullable=False, server_default="zh")
    # Which bundle-agent slug (AgentBundleAgent.slug) is the "principal" —
    # the primary point of contact users should chat with first. Marked with a
    # yellow star in the sidebar at hire time. Optional; when None no agent
    # gets the star (every agent is equal-rank).
    principal_slug: Mapped[str | None] = mapped_column(String(100), default=None, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    agents: Mapped[list["AgentBundleAgent"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
        order_by="AgentBundleAgent.position",
    )
    mcp_servers: Mapped[list["AgentBundleMcpServer"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
    )
    relationships: Mapped[list["AgentBundleRelationship"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
    )


class AgentBundleAgent(Base):
    """One agent definition within a bundle. Maps 1:1 to an Agent row created at hire time."""

    __tablename__ = "agent_bundle_agents"
    __table_args__ = (UniqueConstraint("bundle_id", "slug", name="uq_bundle_agent_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Bundle-local slug, e.g. "bull-researcher". Used to wire relationships
    # and MCP attachments; never exposed to tenants after hire.
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # Display + provisioning order. Agents created in ascending position order.
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_description: Mapped[str] = mapped_column(String(500), default="")
    # Full soul markdown — used verbatim as the new agent's soul.md.
    soul_md: Mapped[str] = mapped_column(Text, default="")
    # Optional preferred model (e.g. "openai/gpt-5.4"). At hire time we fall
    # back to tenant default if the hint isn't available on the hire-er's tenant.
    primary_model_hint: Mapped[str | None] = mapped_column(String(100), default=None)
    # Skill folder names ("gold-data-query"), copied verbatim from bundle dir
    # into the new agent's workspace at hire time. Custom skills ship in the
    # bundle; do NOT register them in the tenant Skill registry.
    default_skills: Mapped[list] = mapped_column(JSON, default=list)
    default_autonomy_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    # List of AgentBundleMcpServer.local_key strings — which bundle MCPs this
    # agent enables (via import_mcp_direct).
    default_mcp_attach: Mapped[list] = mapped_column(JSON, default=list)
    # Per-builtin-tool enable/disable state snapshotted from the source agent.
    # Map of {tool_name: bool} for tools where we want to override the system
    # default (Tool.is_default). Applied after agent creation by upserting
    # AgentTool rows.
    default_tool_toggles: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per-MCP per-tool enable/disable state snapshotted from the source agent.
    # Map of {mcp_local_key: {mcp_tool_name: bool}}. Applied after
    # _bind_bundle_mcps creates AgentTool rows (which default to enabled=True
    # for every discovered MCP tool) — we lookup actual Tool rows by
    # (mcp_server_url, mcp_tool_name) and flip enabled to match the source.
    # This matches 3008's behavior where each tenant agent sees every
    # tenant-installed MCP, with per-agent per-tool enable/disable.
    default_mcp_tool_toggles: Mapped[dict] = mapped_column(JSON, default=dict)

    bundle: Mapped["AgentBundle"] = relationship(back_populates="agents")


class AgentBundleMcpServer(Base):
    """An MCP server attached to a bundle. Registered at hire time per-tenant."""

    __tablename__ = "agent_bundle_mcp_servers"
    __table_args__ = (UniqueConstraint("bundle_id", "local_key", name="uq_bundle_mcp_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Bundle-local key referenced by AgentBundleAgent.default_mcp_attach.
    local_key: Mapped[str] = mapped_column(String(100), nullable=False)
    server_name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Transport hint for the MCPClient probe order. import_mcp_direct
    # auto-detects so this is mostly informational.
    transport: Mapped[str] = mapped_column(String(20), default="streamable-http")

    bundle: Mapped["AgentBundle"] = relationship(back_populates="mcp_servers")


class AgentBundleRelationship(Base):
    """A2A relationship to wire between two bundle agents at hire time."""

    __tablename__ = "agent_bundle_relationships"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id", "from_slug", "to_slug", "relation",
            name="uq_bundle_rel",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Both reference AgentBundleAgent.slug. Resolved to fresh agent_ids at hire time.
    from_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    to_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # Mirrors AgentAgentRelationship.relation values: collaborator | supervisor | assistant | peer | other
    relation: Mapped[str] = mapped_column(String(50), default="collaborator")
    description: Mapped[str] = mapped_column(Text, default="")

    bundle: Mapped["AgentBundle"] = relationship(back_populates="relationships")
