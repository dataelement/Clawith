"""Add is_system column to agents table, and agent_triggers.is_system.

Also adds is_system flag to agent_triggers so that system-created triggers
(e.g. OKR Agent cron trigger) cannot be deleted by users.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "add_agent_is_system"
down_revision = "merge_okr_and_llm_timeout"
branch_labels = None
depends_on = None


def upgrade():
    # Add is_system to agents table
    op.execute("""
        ALTER TABLE agents
        ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # Add is_system to agent_triggers table (system triggers cannot be deleted by users)
    op.execute("""
        ALTER TABLE agent_triggers
        ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade():
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_system")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS is_system")
