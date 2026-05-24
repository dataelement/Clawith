"""Increase api_key_encrypted column length to support Minimax API keys.

Revision ID: increase_api_key_length
Revises: add_notification_agent_id
Create Date: 2026-03-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'increase_api_key_length'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("chat_messages")]
    
    if 'ix_chat_messages_conversation_id' not in indexes:
        op.create_index(
            'ix_chat_messages_conversation_id',
            'chat_messages',
            ['conversation_id'],
            unique=False
        )
    

    columns = inspector.get_columns("llm_models")
    col = next((c for c in columns if c["name"] == "api_key_encrypted"), None)
    
    # Only alter if column length is less than 1024
    if col and (col["type"].length is None or col["type"].length < 1024):
        op.execute("""
            ALTER TABLE llm_models
            ALTER COLUMN api_key_encrypted TYPE VARCHAR(1024)
        """)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = inspector.get_columns("llm_models")
    col = next((c for c in columns if c["name"] == "api_key_encrypted"), None)
    
    if col and col["type"].length == 1024:
        op.execute("""
            ALTER TABLE llm_models
            ALTER COLUMN api_key_encrypted TYPE VARCHAR(500)
        """)
