"""Add agents.is_from_bundle flag to short-circuit per-user onboarding ritual.

Bundle-hired agents ship pre-configured (soul / tools / MCP / A2A). The
generic 4-step "define who I am" / "what's your style" / "your boundaries" /
"finalize" calibration ritual gets injected for every (user, agent) pair the
backend has not seen before — meaning even after the hire-er chats once, any
other org member who later opens a company-visible bundle agent triggers the
ritual again (with ``skip_tools=True``, so the agent appears broken because
its own MCP tools are not exposed to the LLM on the first turn).

This boolean lets the onboarding service short-circuit globally for bundle
agents, regardless of which user is interacting.

Revision ID: add_agent_is_from_bundle
Revises: add_bundle_mcp_tool_toggles
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "add_agent_is_from_bundle"
down_revision = "add_bundle_mcp_tool_toggles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "is_from_bundle",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "is_from_bundle")
