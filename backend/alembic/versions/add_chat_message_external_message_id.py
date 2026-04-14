"""Add external_message_id to chat_messages for channel-native dedupe.

Revision ID: add_chat_msg_ext_id
Revises: add_microsoft_teams_support
"""

from alembic import op


revision = "add_chat_msg_ext_id"
down_revision = "20260313_column_modify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS external_message_id VARCHAR(255)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_external_message_id ON chat_messages(external_message_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_external_message_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS external_message_id")
