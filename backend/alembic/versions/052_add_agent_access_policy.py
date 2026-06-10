"""Add explicit agent access policy fields.

Revision ID: add_agent_access_policy
Revises: add_onboarding_phase
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "add_agent_access_policy"
down_revision: Union[str, Sequence[str], None] = "add_onboarding_phase"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS access_mode VARCHAR(20) NOT NULL DEFAULT 'company'")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS company_access_level VARCHAR(20) NOT NULL DEFAULT 'use'")

    # Backfill explicit mode from the legacy permission rows:
    # company permission -> company-wide; user-only permission -> private.
    op.execute(
        """
        UPDATE agents
        SET access_mode = 'private'
        WHERE id IN (
            SELECT agent_id
            FROM agent_permissions
            GROUP BY agent_id
            HAVING bool_or(scope_type = 'user') AND NOT bool_or(scope_type = 'company')
        )
        """
    )
    op.execute(
        """
        UPDATE agents
        SET company_access_level = COALESCE((
            SELECT ap.access_level
            FROM agent_permissions ap
            WHERE ap.agent_id = agents.id AND ap.scope_type = 'company'
            ORDER BY ap.id
            LIMIT 1
        ), company_access_level)
        WHERE access_mode = 'company'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS company_access_level")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS access_mode")
