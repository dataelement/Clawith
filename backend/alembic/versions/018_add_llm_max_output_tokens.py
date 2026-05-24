"""add llm max_output_tokens

Revision ID: add_llm_max_output_tokens
Revises: add_llm_temperature
Create Date: 2026-03-22 23:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_llm_max_output_tokens'
down_revision = 'add_llm_temperature'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add max_output_tokens column to llm_models table
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER")

def downgrade() -> None:
    # Remove the column
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS max_output_tokens")
