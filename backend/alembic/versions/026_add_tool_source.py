"""Add source to tools and backfill data

Revision ID: add_tool_source
Revises: add_sso_login_enabled
Create Date: 2026-03-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_tool_source'
down_revision: Union[str, None] = 'add_sso_login_enabled'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add source column with a safe default so existing rows get 'builtin' initially
    op.execute("ALTER TABLE tools ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'builtin'")

    # 2. Backfill existing data using the most reliable signals available.

    # (a) Builtin tools (type='builtin') -> source='builtin'
    #     These are already set to 'builtin' by the DEFAULT, but we set explicitly for clarity.
    op.execute("UPDATE tools SET source = 'builtin' WHERE type = 'builtin'")

    # (b) Admin/Company tools: manually created by company admins via the enterprise
    #     settings UI. These are identified by category='custom' AND have a tenant_id.
    #     This distinguishes them from auto-discovered Smithery/MCP tools (category='mcp').
    #     NOTE: Do NOT use `tenant_id IS NOT NULL` alone — agent-installed MCP tools also
    #     carry a tenant_id (to track which company's agent installed them), but they have
    #     category='mcp', not 'custom'.
    op.execute(
        "UPDATE tools SET source = 'admin' "
        "WHERE type = 'mcp' AND category = 'custom' AND tenant_id IS NOT NULL"
    )

    # (c) Agent user-installed tools: all remaining MCP tools still at the DEFAULT 'builtin'.
    #     This includes those with a tenant_id — that field only records which company's
    #     agent installed the tool, it does NOT make it a company-level admin tool.
    op.execute("UPDATE tools SET source = 'agent' WHERE type = 'mcp' AND source = 'builtin'")

    # 3. Fix existing agent_tools.source where incorrectly stored as 'system'.
    #    Any agent_tools row for an agent-installed tool should use 'user_installed'.
    op.execute("""
        UPDATE agent_tools
        SET source = 'user_installed'
        WHERE source = 'system'
          AND tool_id IN (
              SELECT id FROM tools WHERE source = 'agent'
          )
    """)


def downgrade() -> None:
    op.drop_column("tools", "source")
