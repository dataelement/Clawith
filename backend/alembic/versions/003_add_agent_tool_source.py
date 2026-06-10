"""Add source and installed_by_agent_id to agent_tools

Revision ID: add_agent_tool_source
Revises: add_quota_fields
Create Date: 2026-03-06
"""
from alembic import op
import sqlalchemy as sa

revision = "add_agent_tool_source"
down_revision = "add_quota_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'system'")
    op.execute("ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS installed_by_agent_id UUID")


def downgrade() -> None:
    op.drop_column("agent_tools", "installed_by_agent_id")
    op.drop_column("agent_tools", "source")
