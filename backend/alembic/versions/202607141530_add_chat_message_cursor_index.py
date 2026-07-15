"""Add the durable chat message cursor lookup index.

Revision ID: add_chat_message_cursor_index
Revises: create_channel_delivery_outbox
Create Date: 2026-07-14 15:30:00

``chat_messages`` already has separate indexes for ``conversation_id`` and
``created_at``. Cursor history reads constrain the conversation and order by
``(created_at, id)``, so neither single-column index covers that access path.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "add_chat_message_cursor_index"
down_revision: str | None = "create_channel_delivery_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_chat_messages_conversation_created_id"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "chat_messages",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="chat_messages")
