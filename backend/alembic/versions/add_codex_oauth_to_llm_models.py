"""Add Codex OAuth columns to llm_models and make api_key_encrypted nullable.

Revision ID: add_codex_oauth_to_llm_models
Revises: increase_api_key_length
Create Date: 2026-04-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_codex_oauth_to_llm_models"
down_revision: Union[str, None] = "increase_api_key_length"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OAuth-mode models don't have a static api_key, so relax the NOT NULL
    op.execute("ALTER TABLE llm_models ALTER COLUMN api_key_encrypted DROP NOT NULL")

    op.add_column(
        "llm_models",
        sa.Column(
            "auth_type",
            sa.String(20),
            nullable=False,
            server_default="static",
        ),
    )
    op.add_column(
        "llm_models",
        sa.Column("oauth_access_token_encrypted", sa.String(4096), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("oauth_refresh_token_encrypted", sa.String(1024), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("oauth_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("oauth_account_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_models", "oauth_account_id")
    op.drop_column("llm_models", "oauth_expires_at")
    op.drop_column("llm_models", "oauth_refresh_token_encrypted")
    op.drop_column("llm_models", "oauth_access_token_encrypted")
    op.drop_column("llm_models", "auth_type")
    # Revert to NOT NULL; assumes no rows with null api_key_encrypted remain.
    op.execute("ALTER TABLE llm_models ALTER COLUMN api_key_encrypted SET NOT NULL")
