"""add llm temperature

Revision ID: add_llm_temperature
Revises: 
Create Date: 2026-03-22 19:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_llm_temperature'
down_revision = 'add_notification_agent_id'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add temperature column to llm_models table
    # Using IF NOT EXISTS is not standard in generic SQLAlchemy additions unless we execute raw SQL
    # so we'll execute a raw SQL to be safe on PostgreSQL and avoid idempotency issues if run multiple times
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS temperature FLOAT")

def downgrade() -> None:
    # Remove the column
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS temperature")
