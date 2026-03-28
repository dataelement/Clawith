"""Add sso_login_enabled to identity_providers

Revision ID: add_sso_login_enabled
Revises:
Create Date: 2026-03-29
"""
from alembic import op
import sqlalchemy as sa

revision = "add_sso_login_enabled"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sso_login_enabled column to identity_providers table
    # Default is False: existing providers only do directory sync, not SSO login
    op.add_column(
        "identity_providers",
        sa.Column(
            "sso_login_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("identity_providers", "sso_login_enabled")
