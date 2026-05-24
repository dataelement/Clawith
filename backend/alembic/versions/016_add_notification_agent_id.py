"""Add agent_id and sender_name to notifications table.

Revision ID: add_notification_agent_id
Revises: add_published_pages
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_notification_agent_id'
down_revision: Union[str, None] = 'add_published_pages'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safely alter only if table exists
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'notifications') THEN
            ALTER TABLE notifications ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id);
            ALTER TABLE notifications ADD COLUMN IF NOT EXISTS sender_name VARCHAR(100);
            ALTER TABLE notifications ALTER COLUMN user_id DROP NOT NULL;
        END IF;
    END
    $$;
    """)
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'notifications') THEN
            CREATE INDEX IF NOT EXISTS ix_notifications_agent_id ON notifications(agent_id) WHERE agent_id IS NOT NULL;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_agent_id")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS agent_id")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS sender_name")
    op.execute("ALTER TABLE notifications ALTER COLUMN user_id SET NOT NULL")
