"""Add workspace file revision and edit lock tables.

Revision ID: add_workspace_revisions
Revises: okr_agent_id_sys_uq
Create Date: 2026-04-15
"""
from typing import Sequence, Union

from alembic import op


revision: str = "add_workspace_revisions"
down_revision: Union[str, None] = "okr_agent_id_sys_uq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_file_revisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            path VARCHAR(500) NOT NULL,
            operation VARCHAR(40) NOT NULL DEFAULT 'write',
            actor_type VARCHAR(20) NOT NULL,
            actor_id UUID,
            session_id VARCHAR(200),
            before_content TEXT,
            after_content TEXT,
            content_hash VARCHAR(64) NOT NULL DEFAULT '',
            group_key VARCHAR(200),
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_file_revisions_agent_id ON workspace_file_revisions(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_file_revisions_path ON workspace_file_revisions(path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_file_revisions_group_key ON workspace_file_revisions(group_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_file_revisions_created_at ON workspace_file_revisions(created_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_edit_locks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            path VARCHAR(500) NOT NULL,
            user_id UUID NOT NULL REFERENCES users(id),
            session_id VARCHAR(200),
            expires_at TIMESTAMPTZ NOT NULL,
            heartbeat_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_workspace_edit_locks_agent_path UNIQUE (agent_id, path)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_edit_locks_agent_id ON workspace_edit_locks(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_edit_locks_path ON workspace_edit_locks(path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_edit_locks_user_id ON workspace_edit_locks(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_edit_locks_expires_at ON workspace_edit_locks(expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_edit_locks")
    op.execute("DROP TABLE IF EXISTS workspace_file_revisions")
