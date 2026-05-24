"""add_title_to_agent_focus_items

Revision ID: 043e5f59eb8a
Revises: add_user_tenant_onboarding
Create Date: 2026-05-22 21:02:18.556576
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_title_to_agent_focus_items'
down_revision: Union[str, None] = 'merge_heads_20260521'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add title column to agent_focus_items
    op.add_column('agent_focus_items', sa.Column('title', sa.String(length=200), nullable=True))


def downgrade() -> None:
    # Remove title column from agent_focus_items
    op.drop_column('agent_focus_items', 'title')
