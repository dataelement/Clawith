"""Add invitation_codes table.

This is an idempotent migration — uses CREATE TABLE IF NOT EXISTS.
"""

from alembic import op
import sqlalchemy as sa

revision = "add_invitation_codes"
down_revision = "add_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS invitation_codes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        code VARCHAR(32) NOT NULL UNIQUE,
        max_uses INTEGER NOT NULL DEFAULT 1,
        used_count INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_by UUID REFERENCES users(id),
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_invitation_codes_code ON invitation_codes(code)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invitation_codes")
