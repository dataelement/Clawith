"""Add agent_type, api_key_hash, openclaw_last_seen to agents (source-deploy sync).

Fixes #84: these columns were only patched in Docker entrypoint.sh; source
deployment users never got them, causing "column agents.agent_type does not exist".

Revision ID: add_agent_type_columns
Revises: 20260313_column_modify
"""
from alembic import op

revision = "add_agent_type_columns"
down_revision = "20260313_column_modify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_type VARCHAR(20) NOT NULL DEFAULT 'native'"
    )
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(128)")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS openclaw_last_seen TIMESTAMPTZ")


def downgrade() -> None:
    # Optional: drop columns if downgrading (PostgreSQL 9.6+ supports IF EXISTS)
    pass
