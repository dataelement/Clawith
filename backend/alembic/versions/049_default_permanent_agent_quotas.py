"""Set default agent quotas to permanent TTL and higher daily LLM calls.

Revision ID: default_permanent_agent_quotas
Revises: merge_pr494_heads
Create Date: 2026-04-30
"""

from typing import Sequence, Union

from alembic import op


revision: str = "default_permanent_agent_quotas"
down_revision: Union[str, Sequence[str], None] = "merge_pr494_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN quota_agent_ttl_hours SET DEFAULT 0")
    op.execute("ALTER TABLE agents ALTER COLUMN max_llm_calls_per_day SET DEFAULT 1000")
    op.execute("ALTER TABLE tenants ALTER COLUMN default_agent_ttl_hours SET DEFAULT 0")
    op.execute("ALTER TABLE tenants ALTER COLUMN default_max_llm_calls_per_day SET DEFAULT 1000")

    # Move records that still use the old shipped defaults onto the new defaults.
    op.execute("UPDATE users SET quota_agent_ttl_hours = 0 WHERE quota_agent_ttl_hours = 48")
    op.execute("UPDATE tenants SET default_agent_ttl_hours = 0 WHERE default_agent_ttl_hours = 48")
    op.execute("UPDATE tenants SET default_max_llm_calls_per_day = 1000 WHERE default_max_llm_calls_per_day = 100")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN quota_agent_ttl_hours SET DEFAULT 48")
    op.execute("ALTER TABLE agents ALTER COLUMN max_llm_calls_per_day SET DEFAULT 100")
    op.execute("ALTER TABLE tenants ALTER COLUMN default_agent_ttl_hours SET DEFAULT 48")
    op.execute("ALTER TABLE tenants ALTER COLUMN default_max_llm_calls_per_day SET DEFAULT 100")
