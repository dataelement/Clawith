"""add llm concurrency_group

Revision ID: add_llm_concurrency_group
Revises: a1b2c3d4e5f6
Create Date: 2026-04-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "add_llm_concurrency_group"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS concurrency_group VARCHAR(100)")

def downgrade() -> None:
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS concurrency_group")
