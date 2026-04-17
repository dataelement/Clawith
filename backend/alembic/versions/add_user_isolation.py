"""add user_isolation_enabled to agents table

Revision ID: add_user_isolation
Revises: previous_revision
Create Date: 2026-04-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_user_isolation'
down_revision = None  # TODO: Set to previous revision
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add user_isolation_enabled column to agents table
    op.add_column('agents', sa.Column(
        'user_isolation_enabled',
        sa.Boolean(),
        nullable=False,
        server_default='true',
        comment='Enable user-specific workspace isolation for multi-user scenarios'
    ))


def downgrade() -> None:
    op.drop_column('agents', 'user_isolation_enabled')
