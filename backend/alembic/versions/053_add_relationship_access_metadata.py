"""Add relationship access metadata.

Revision ID: add_relationship_access_metadata
Revises: add_agent_access_policy
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "add_relationship_access_metadata"
down_revision: Union[str, Sequence[str], None] = "add_agent_access_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_relationships ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agent_relationships ADD COLUMN IF NOT EXISTS created_by_user_id UUID")
    op.execute("ALTER TABLE agent_relationships ADD COLUMN IF NOT EXISTS updated_by_user_id UUID")
    op.execute("ALTER TABLE agent_agent_relationships ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agent_agent_relationships ADD COLUMN IF NOT EXISTS created_by_user_id UUID")
    op.execute("ALTER TABLE agent_agent_relationships ADD COLUMN IF NOT EXISTS updated_by_user_id UUID")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_agent_relationships DROP COLUMN IF EXISTS updated_by_user_id")
    op.execute("ALTER TABLE agent_agent_relationships DROP COLUMN IF EXISTS created_by_user_id")
    op.execute("ALTER TABLE agent_agent_relationships DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE agent_relationships DROP COLUMN IF EXISTS updated_by_user_id")
    op.execute("ALTER TABLE agent_relationships DROP COLUMN IF EXISTS created_by_user_id")
    op.execute("ALTER TABLE agent_relationships DROP COLUMN IF EXISTS updated_at")
