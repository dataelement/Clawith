"""Add structured agent focus items.

Revision ID: add_agent_focus_items
Revises: web_provider_platform
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "add_agent_focus_items"
down_revision: Union[str, None] = "web_provider_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_focus_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            key VARCHAR(200) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status VARCHAR(24) NOT NULL DEFAULT 'in_progress',
            kind VARCHAR(24) NOT NULL DEFAULT 'normal',
            source VARCHAR(40) NOT NULL DEFAULT 'user',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            sort_order INTEGER NOT NULL DEFAULT 0,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_agent_focus_items_agent_key UNIQUE (agent_id, key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_agent_id ON agent_focus_items(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_key ON agent_focus_items(key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_status ON agent_focus_items(status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_kind ON agent_focus_items(kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_source ON agent_focus_items(source)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_focus_items_created_at ON agent_focus_items(created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_focus_items")
