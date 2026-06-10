"""Add token cache usage counters.

Revision ID: add_token_cache_usage_fields
Revises: default_permanent_agent_quotas
Create Date: 2026-05-02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "add_token_cache_usage_fields"
down_revision: Union[str, Sequence[str], None] = "default_permanent_agent_quotas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in [
        "cache_read_tokens_today",
        "cache_read_tokens_month",
        "cache_read_tokens_total",
        "cache_creation_tokens_today",
        "cache_creation_tokens_month",
        "cache_creation_tokens_total",
    ]:
        op.execute(f"ALTER TABLE agents ADD COLUMN IF NOT EXISTS {column} INTEGER DEFAULT 0")

    for column in [
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "estimated_tokens",
    ]:
        op.execute(f"ALTER TABLE daily_token_usage ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    for column in [
        "estimated_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "output_tokens",
        "input_tokens",
    ]:
        op.execute(f"ALTER TABLE daily_token_usage DROP COLUMN IF EXISTS {column}")

    for column in [
        "cache_creation_tokens_total",
        "cache_creation_tokens_month",
        "cache_creation_tokens_today",
        "cache_read_tokens_total",
        "cache_read_tokens_month",
        "cache_read_tokens_today",
    ]:
        op.execute(f"ALTER TABLE agents DROP COLUMN IF EXISTS {column}")
