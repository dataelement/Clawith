"""Merge OKR tables branch with llm_request_timeout branch.

This merge migration resolves the dual-head situation that arose because
add_okr_tables and d9cbd43b62e5 (add_llm_request_timeout) were created
in parallel branches. After this merge, the history is a single linear
chain again.

Revision ID: merge_okr_and_llm_timeout
Revises: add_okr_tables, d9cbd43b62e5
"""

from alembic import op

# revision identifiers
revision = "merge_okr_and_llm_timeout"
down_revision = ("add_okr_tables", "d9cbd43b62e5")
branch_labels = None
depends_on = None


def upgrade():
    # Pure merge — no schema changes needed.
    pass


def downgrade():
    # Pure merge — no schema changes to revert.
    pass
