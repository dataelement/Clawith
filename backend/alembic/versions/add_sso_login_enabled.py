"""Add sso_login_enabled to identity_providers

Revision ID: add_sso_login_enabled
Revises: user_refactor_v1
Create Date: 2026-03-29
"""
from alembic import op
import sqlalchemy as sa

revision = "add_sso_login_enabled"
down_revision = "user_refactor_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sso_login_enabled column to identity_providers table
    # Default is False: existing providers only do directory sync, not SSO login
    op.execute("ALTER TABLE identity_providers ADD COLUMN IF NOT EXISTS sso_login_enabled BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.drop_column("identity_providers", "sso_login_enabled")
