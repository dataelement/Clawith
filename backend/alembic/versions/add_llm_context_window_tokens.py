"""add llm context_window_tokens

Revision ID: add_llm_context_window_tokens
Revises: add_user_tenant_onboarding
Create Date: 2026-05-12 00:00:00.000000
"""

from alembic import op


revision = "add_llm_context_window_tokens"
down_revision = "add_user_tenant_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS context_window_tokens INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS context_window_tokens")
