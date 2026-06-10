"""Add agent token usage and context fields to agents table.

Revision ID: add_agent_usage_fields
"""

from alembic import op

revision = "add_agent_usage_fields"
down_revision = "add_agent_triggers"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Agents table: token usage and context fields
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_tokens_per_day INTEGER")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_tokens_per_month INTEGER")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_today INTEGER DEFAULT 0 NOT NULL")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_month INTEGER DEFAULT 0 NOT NULL")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS context_window_size INTEGER DEFAULT 100 NOT NULL")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_tool_rounds INTEGER DEFAULT 50 NOT NULL")

def downgrade() -> None:
    pass
