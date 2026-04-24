"""Per-(user, agent) onboarding junction table + drop legacy bootstrapped flag.

Revision ID: add_agent_user_onboardings
Revises: add_tenant_default_model
Create Date: 2026-04-24

A row in agent_user_onboardings records that a user has been onboarded to a
specific agent. Its presence is the authoritative signal that onboarding
should NOT fire again for that pair — regardless of whether the user
actually finished the introduction flow.

Backfill: every (agent_id, user_id) pair that has any historical chat message
is inserted with onboarded_at = the earliest message. Existing users thus
never get retroactively re-onboarded.

Also drops the short-lived Agent.bootstrapped column that an earlier draft
of this feature introduced — the per-user model replaces it entirely. The
drop is idempotent so fresh installs (which no longer add the column in
add_agent_bootstrap_fields) aren't affected.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'add_agent_user_onboardings'
down_revision: Union[str, None] = 'add_tenant_default_model'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_user_onboardings (
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            onboarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (agent_id, user_id)
        )
    """)

    # Backfill from chat history: any pair that has ever exchanged messages is
    # considered already onboarded — don't re-greet established relationships.
    op.execute("""
        INSERT INTO agent_user_onboardings (agent_id, user_id, onboarded_at)
        SELECT agent_id, user_id, MIN(created_at)
        FROM chat_messages
        WHERE agent_id IS NOT NULL AND user_id IS NOT NULL
        GROUP BY agent_id, user_id
        ON CONFLICT DO NOTHING
    """)

    # Clean up the abandoned per-agent flag from the previous design iteration.
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS bootstrapped")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_user_onboardings")
