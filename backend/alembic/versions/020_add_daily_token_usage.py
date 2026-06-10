"""Add daily_token_usage table.

Revision ID: add_daily_token_usage
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "add_daily_token_usage"
down_revision = "add_agentbay_enum_value"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS to safely handle instances where the table already exists
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_token_usage (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            agent_id UUID NOT NULL REFERENCES agents(id),
            date TIMESTAMPTZ NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_token_usage_agent_date
        ON daily_token_usage (agent_id, date)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_token_usage_tenant_id ON daily_token_usage (tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_token_usage_agent_id ON daily_token_usage (agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_token_usage_date ON daily_token_usage (date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_token_usage")
