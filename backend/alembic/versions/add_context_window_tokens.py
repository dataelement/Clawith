"""add agents.context_window_tokens for token-aware history truncation

Revision ID: add_context_window_tokens
Revises: rm_agent_credential_secrets
Create Date: 2026-04-27
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "add_context_window_tokens"
down_revision: Union[str, Sequence[str], None] = "rm_agent_credential_secrets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add context_window_tokens with a DDL default of 50000.

    The four-step pattern is required because earlier in the migration chain,
    ``alembic/versions/0000_initial_schema.py`` calls
    ``Base.metadata.create_all(checkfirst=True)``, which creates ``agents``
    from the *current* model state — including any new columns. SQLAlchemy's
    Python-side ``default=`` does NOT translate to a DDL ``DEFAULT`` clause,
    so the column ends up ``NOT NULL`` with no default, and a naive
    ``ADD COLUMN IF NOT EXISTS ... DEFAULT 50000`` short-circuits and never
    sets the default.

    This four-step approach is idempotent regardless of pre-existing state:
      - column missing → created (nullable, no default initially)
      - column present without default → default set
      - any rows with NULL → backfilled to 50000
      - column made NOT NULL

    Re-runnable: ALTER SET DEFAULT to the same value is a no-op; UPDATE
    affecting 0 rows is a no-op; ALTER SET NOT NULL on an already-NOT-NULL
    column is a no-op.
    """
    # 1. Add the column if missing — do NOT specify NOT NULL or DEFAULT here,
    #    so existing rows (if any from create_all) aren't blocked.
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS context_window_tokens INTEGER"
    )
    # 2. Ensure the DDL default is set so future inserts that omit this
    #    column (raw SQL, restored backups, manual migrations) get 50000.
    op.execute(
        "ALTER TABLE agents ALTER COLUMN context_window_tokens SET DEFAULT 50000"
    )
    # 3. Backfill any rows that were created before the default landed.
    op.execute(
        "UPDATE agents SET context_window_tokens = 50000 "
        "WHERE context_window_tokens IS NULL"
    )
    # 4. Now safe to enforce NOT NULL.
    op.execute(
        "ALTER TABLE agents ALTER COLUMN context_window_tokens SET NOT NULL"
    )


def downgrade() -> None:
    # Downgrade omitted — dropping the column would lose per-tenant tuning.
    pass
