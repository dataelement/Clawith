"""add_group_chat_fields_to_chat_sessions

Add is_group and group_name columns to chat_sessions table
for proper group chat session isolation (Issue #182).

Revision ID: a1b2c3d4e5f6
Revises: be48e94fa052
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "be48e94fa052"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = [c["name"] for c in inspector.get_columns("chat_sessions")]

    # Add is_group boolean column (default false) — skip if already exists
    if "is_group" not in existing_cols:
        op.add_column(
            "chat_sessions",
            sa.Column("is_group", sa.Boolean(), server_default="false", nullable=False),
        )
    # Add group_name column for display purposes — skip if already exists
    if "group_name" not in existing_cols:
        op.add_column(
            "chat_sessions",
            sa.Column("group_name", sa.String(200), nullable=True),
        )


def downgrade():
    op.drop_column("chat_sessions", "group_name")
    op.drop_column("chat_sessions", "is_group")
