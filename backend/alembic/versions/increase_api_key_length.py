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
    # Increase api_key_encrypted column length from 500 to 1024
    # Minimax API keys are very long and exceed the previous 500 char limit
    op.execute("""
        ALTER TABLE llm_models
        ALTER COLUMN api_key_encrypted TYPE VARCHAR(1024)
    """)


def downgrade() -> None:
    # Revert to 500 chars (may fail if data exceeds 500 chars)
    op.execute("""
        ALTER TABLE llm_models
        ALTER COLUMN api_key_encrypted TYPE VARCHAR(500)
    """)
