"""Add default_tool_toggles JSON column to agent_bundle_agents.

Bundles can now snapshot the source agent's per-builtin-tool enable/disable
state. Applied at hire time by upserting AgentTool rows so the new agent
matches the source's toggle profile (instead of falling back to the system
default which makes every category fully enabled).

Revision ID: add_bundle_tool_toggles
Revises: add_agent_bundles
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "add_bundle_tool_toggles"
# Multi-parent: also acts as the merge between our `add_agent_bundles` line
# and upstream's `add_user_tenant_onboarding` line (both branch off
# `add_agent_focus_items`). Without this, `alembic upgrade head` complains
# about multiple heads.
down_revision: Union[str, Sequence[str], None] = ("add_agent_bundles", "add_user_tenant_onboarding")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_bundle_agents",
        sa.Column(
            "default_tool_toggles",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_bundle_agents", "default_tool_toggles")
