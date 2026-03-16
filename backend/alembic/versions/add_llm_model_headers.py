"""Add headers_encrypted column to llm_models table.

Idempotent — uses IF NOT EXISTS for ALTER statement.

Revision ID: add_llm_model_headers
Revises: 20260313_column_modify
"""

from alembic import op

revision = "add_llm_model_headers"
down_revision = "multi_tenant_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS headers_encrypted TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS headers_encrypted")
