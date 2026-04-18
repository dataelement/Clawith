"""add_user_group_permission_scope

Revision ID: add_user_group_scope
Revises: increase_api_key_length
Create Date: 2026-04-16 22:30:00.000000

This migration adds 'user_group' enum value for specifying multiple users with permissions.
'user' remains as "only creator" (backward compatible).
'user_group' is the new "specific users" option.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_user_group_scope'
down_revision: Union[str, None] = 'increase_api_key_length'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'user_group' to the permission_scope_enum enum type
    op.execute("ALTER TYPE permission_scope_enum ADD VALUE IF NOT EXISTS 'user_group'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values easily
    pass
