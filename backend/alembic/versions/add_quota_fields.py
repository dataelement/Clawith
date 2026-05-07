"""Add usage quota fields to users, agents, and tenants tables.

Idempotent — uses IF NOT EXISTS for all ALTER statements.
"""

from alembic import op

revision = "add_quota_fields"
down_revision = "initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users table: quota fields
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_limit INTEGER DEFAULT 50")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_period VARCHAR(20) DEFAULT 'permanent'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_messages_used INTEGER DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_max_agents INTEGER DEFAULT 2")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_agent_ttl_hours INTEGER DEFAULT 0")

    # Agents table: expiry + LLM call tracking
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_today INTEGER DEFAULT 0")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_llm_calls_per_day INTEGER DEFAULT 1000")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_reset_at TIMESTAMPTZ")

    # Tenants table: default quotas + heartbeat floor
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_message_limit INTEGER DEFAULT 50")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_message_period VARCHAR(20) DEFAULT 'permanent'")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_agents INTEGER DEFAULT 2")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_agent_ttl_hours INTEGER DEFAULT 0")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_llm_calls_per_day INTEGER DEFAULT 1000")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS min_heartbeat_interval_minutes INTEGER DEFAULT 120")


def downgrade() -> None:
    pass  # Not reversible safely (columns may have data)
