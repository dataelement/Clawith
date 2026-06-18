"""merge bundles + focus title heads

Trivial merge of two alembic heads that diverged after `merge_pr494_heads`:

- upstream lineage: ... -> 059_add_title_to_agent_focus_items (adds `title`
  column to agent_focus_items)
- bundle lineage:   ... -> add_agent_bundle_group_fields (adds
  `bundle_hire_group_id` + `is_bundle_principal` to agents)

Both branches are independent — no schema overlap, no data conflict — so
the merge is a no-op revision that just unifies the heads. Alembic refuses
`upgrade head` while multiple heads exist; this revision restores a single
head.

Revision ID: merge_bundles_focus_title
Revises: 059_add_title_to_agent_focus_items, add_agent_bundle_group_fields
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "merge_bundles_focus_title"
down_revision: Union[str, Sequence[str], None] = (
    "add_title_to_agent_focus_items",
    "add_agent_bundle_group_fields",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Pure merge — no schema changes."""
    pass


def downgrade() -> None:
    """Pure merge — no schema changes to undo."""
    pass
