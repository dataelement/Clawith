"""Add language column to agent_bundles.

Author-declared natural language of the bundle's agent content (soul.md, skill
files, agent names). Used by the frontend Talent Market to filter bundles by
current UI locale — EN users see only EN-native bundles, CN users only CN-native.

Existing rows backfill to ``"zh"`` since all pre-existing builtin bundles are
Chinese-native.

Revision ID: add_bundle_language
Revises: merge_bundles_focus_title
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_bundle_language"
down_revision: Union[str, Sequence[str], None] = "merge_bundles_focus_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_bundles",
        sa.Column(
            "language",
            sa.String(length=8),
            nullable=False,
            server_default="zh",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_bundles", "language")
