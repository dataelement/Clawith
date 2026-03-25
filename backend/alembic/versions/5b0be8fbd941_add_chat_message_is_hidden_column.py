"""add chat_message is_hidden column

Revision ID: 5b0be8fbd941
Revises: add_llm_max_output_tokens
Create Date: 2026-03-25 21:47:36.761546
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5b0be8fbd941'
down_revision: Union[str, None] = 'add_llm_max_output_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_messages', sa.Column('is_hidden', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('chat_messages', 'is_hidden')
