"""Add chat_sessions table and update existing chat_messages conversation_ids."""

import uuid
import sqlalchemy as sa
from alembic import op

revision = "add_chat_sessions"
down_revision = "add_agent_tool_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create chat_sessions table (idempotent)
    op.execute("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_id UUID NOT NULL REFERENCES agents(id),
        user_id UUID NOT NULL REFERENCES users(id),
        title VARCHAR(200) NOT NULL DEFAULT 'New Session',
        source_channel VARCHAR(20) NOT NULL DEFAULT 'web',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        last_message_at TIMESTAMPTZ
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_agent_id ON chat_sessions (agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON chat_sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_created_at ON chat_sessions (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_sessions")
