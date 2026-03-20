"""add published_pages table

Revision ID: add_published_pages
Revises: df3da9cf3b27
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'add_published_pages'
down_revision: Union[str, None] = 'df3da9cf3b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS published_pages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            short_id VARCHAR(16) NOT NULL UNIQUE,
            agent_id UUID NOT NULL REFERENCES agents(id),
            user_id UUID NOT NULL REFERENCES users(id),
            tenant_id UUID REFERENCES tenants(id),
            source_path VARCHAR(500) NOT NULL,
            title VARCHAR(200) DEFAULT '',
            view_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_published_pages_short_id ON published_pages(short_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_published_pages_agent_id ON published_pages(agent_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS published_pages")
