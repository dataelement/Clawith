"""Add bootstrap_content + capability_bullets to agent templates.

Revision ID: add_agent_bootstrap_fields
Revises: increase_api_key_length
Create Date: 2026-04-23

Supports the Talent Market (capability_bullets fuel the template cards) and
the per-user onboarding ritual (bootstrap_content is the founder-facing
system prompt). The per-agent Agent.bootstrapped flag that earlier drafts
carried has been dropped in favour of the per-user agent_user_onboardings
junction table — see the add_agent_user_onboardings migration.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'add_agent_bootstrap_fields'
down_revision: Union[str, None] = 'increase_api_key_length'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS capability_bullets JSON DEFAULT '[]'::json")
    op.execute("ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS bootstrap_content TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_templates DROP COLUMN IF EXISTS bootstrap_content")
    op.execute("ALTER TABLE agent_templates DROP COLUMN IF EXISTS capability_bullets")
