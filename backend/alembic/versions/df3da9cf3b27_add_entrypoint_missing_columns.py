"""add entrypoint missing columns

Revision ID: df3da9cf3b27
Revises: multi_tenant_registration
Create Date: 2026-03-17 00:13:29.200426
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df3da9cf3b27'
down_revision: Union[str, None] = 'multi_tenant_registration'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use IF NOT EXISTS to ensure idempotence and compatibility with databases
    # that already had these columns added via Docker entrypoint.sh.
    # We must use execute() for raw SQL because Alembic's add_column does not
    # natively support IF NOT EXISTS in all environments gracefully.

    # 1. Users table (Quota management)
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_limit INTEGER DEFAULT 50")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_period VARCHAR(20) DEFAULT 'permanent'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_messages_used INTEGER DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_max_agents INTEGER DEFAULT 2")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_agent_ttl_hours INTEGER DEFAULT 48")

    # 2. Agents table (OpenClaw, tokens, limits, retention)
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_today INTEGER DEFAULT 0")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_llm_calls_per_day INTEGER DEFAULT 100")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_reset_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_daily_reset TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_monthly_reset TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_total INTEGER DEFAULT 0")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_type VARCHAR(20) NOT NULL DEFAULT 'native'")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(128)")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS openclaw_last_seen TIMESTAMPTZ")

    # 3. AgentTools table (Tool assignment tracking)
    op.execute("ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'system'")
    op.execute("ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS installed_by_agent_id UUID")

    # 4. ChatSessions table (Channel source parsing)
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS source_channel VARCHAR(20) NOT NULL DEFAULT 'web'")


def downgrade() -> None:
    # Downgrading is omitted intentionally. These columns are structurally
    # integrated into the ORM models and dropping them would cause data loss.
    pass
