"""Merge heads: add_user_tenant_onboarding and perf_indexes_20260512.

Revision ID: merge_heads_20260521
Revises: add_user_tenant_onboarding, perf_indexes_20260512
Create Date: 2026-05-21
"""

from typing import Sequence, Union


revision: str = "merge_heads_20260521"
down_revision: Union[str, Sequence[str], None] = (
    "add_user_tenant_onboarding",
    "perf_indexes_20260512",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
