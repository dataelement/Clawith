"""Add bridge_adapter column to agents for per-agent local runtime selection.

When agent_type='openclaw', bridge_adapter picks which local runtime the
downloaded bridge installer + session.start.adapter will target:
'claude_code' | 'openclaw' | 'hermes'.

Backfill policy: existing openclaw agents get 'claude_code' (the de-facto
default TOML was only enabling claude_code).

Revision ID: add_bridge_adapter
Revises: add_bridge_mode
Create Date: 2026-04-22
"""
from alembic import op


revision = "add_bridge_adapter"
down_revision = "add_bridge_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS bridge_adapter VARCHAR(32)"
    )
    op.execute(
        "UPDATE agents SET bridge_adapter='claude_code' "
        "WHERE agent_type='openclaw' AND bridge_adapter IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS bridge_adapter")
