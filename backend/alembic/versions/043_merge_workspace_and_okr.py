"""Merge merge_okr_api_key and add_workspace_revisions heads.

Revision ID: merge_workspace_and_okr
Revises: merge_okr_api_key, add_workspace_revisions
Create Date: 2026-04-15
"""
from typing import Sequence, Union

revision: str = "merge_workspace_and_okr"
down_revision: Union[str, Sequence[str], None] = (
    "merge_okr_api_key",
    "add_workspace_revisions",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
