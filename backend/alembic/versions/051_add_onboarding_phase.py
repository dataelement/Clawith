"""Add phase tracking to agent/user onboarding.

Revision ID: add_onboarding_phase
Revises: add_token_cache_usage_fields
Create Date: 2026-05-03
"""

from typing import Sequence, Union

from alembic import op


revision: str = "add_onboarding_phase"
down_revision: Union[str, Sequence[str], None] = "add_token_cache_usage_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_user_onboardings "
        "ADD COLUMN IF NOT EXISTS phase VARCHAR(32) NOT NULL DEFAULT 'completed'"
    )
    op.execute(
        "UPDATE agent_user_onboardings SET phase = 'completed' "
        "WHERE phase IS NULL OR phase = ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_user_onboardings DROP COLUMN IF EXISTS phase")
