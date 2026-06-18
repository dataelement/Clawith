"""Add default_mcp_tool_toggles JSON column to agent_bundle_agents.

Stores per-MCP per-tool enable/disable state snapshotted from the source agent.
Applied at hire time after _bind_bundle_mcps to make new agents match source's
per-MCP-tool toggle profile (instead of all-enabled default from import_mcp_direct).

Revision ID: add_bundle_mcp_tool_toggles
Revises: add_bundle_tool_toggles
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "add_bundle_mcp_tool_toggles"
down_revision: Union[str, Sequence[str], None] = "add_bundle_tool_toggles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_bundle_agents",
        sa.Column(
            "default_mcp_tool_toggles",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_bundle_agents", "default_mcp_tool_toggles")
