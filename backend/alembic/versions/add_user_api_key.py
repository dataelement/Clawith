"""Add api_key_hash column to users table for user-level API key support.

Revision ID: add_user_api_key
"""

from alembic import op


revision = "add_user_api_key"
down_revision = "user_refactor_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(64) UNIQUE
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_users_api_key_hash ON users(api_key_hash)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_api_key_hash")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS api_key_hash")
