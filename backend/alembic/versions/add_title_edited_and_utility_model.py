"""Add title_edited to chat_sessions and utility_model_id to tenants.

Revision ID: add_title_edited_and_utility_model
Revises: 5b0be8fbd941
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "add_title_edited_and_utility_model"
down_revision = "5b0be8fbd941"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "title_edited BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        "utility_model_id UUID REFERENCES llm_models(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS utility_model_id")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS title_edited")
