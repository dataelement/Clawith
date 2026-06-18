"""Add agent_bundles + agent_bundle_agents + agent_bundle_mcp_servers + agent_bundle_relationships.

Revision ID: add_agent_bundles
Revises: merge_pr494_heads
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "add_agent_bundles"
# Multi-parent: also acts as the merge of v1.9.3's two existing heads
# (merge_pr494_heads + add_agent_focus_items) so `alembic upgrade head`
# resolves to a single head after this migration.
down_revision: Union[str, Sequence[str], None] = ("merge_pr494_heads", "add_agent_focus_items")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_bundles ─ parent
    op.create_table(
        "agent_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("icon", sa.String(50), nullable=False, server_default="TM"),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column(
            "capability_bullets",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("version", sa.String(20), nullable=False, server_default="0.1.0"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="uq_agent_bundles_slug"),
    )

    # agent_bundle_agents ─ N agent definitions per bundle
    op.create_table(
        "agent_bundle_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role_description", sa.String(500), nullable=False, server_default=""),
        sa.Column("soul_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_model_hint", sa.String(100), nullable=True),
        sa.Column(
            "default_skills",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "default_autonomy_policy",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "default_mcp_attach",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.UniqueConstraint("bundle_id", "slug", name="uq_bundle_agent_slug"),
    )

    # agent_bundle_mcp_servers ─ MCP attachments declared by bundle
    op.create_table(
        "agent_bundle_mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_key", sa.String(100), nullable=False),
        sa.Column("server_name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("transport", sa.String(20), nullable=False, server_default="streamable-http"),
        sa.UniqueConstraint("bundle_id", "local_key", name="uq_bundle_mcp_key"),
    )

    # agent_bundle_relationships ─ internal A2A graph (may be empty)
    op.create_table(
        "agent_bundle_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_slug", sa.String(100), nullable=False),
        sa.Column("to_slug", sa.String(100), nullable=False),
        sa.Column("relation", sa.String(50), nullable=False, server_default="collaborator"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "bundle_id", "from_slug", "to_slug", "relation",
            name="uq_bundle_rel",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_bundle_relationships")
    op.drop_table("agent_bundle_mcp_servers")
    op.drop_table("agent_bundle_agents")
    op.drop_table("agent_bundles")
