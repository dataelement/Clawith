"""Join the v1.11.4 timezone and Tool Runtime migration branches.

Revision ID: f063_merge_v1_11_4_heads
Revises: f061_default_tenant_timezone, f062_tool_execution_identity
Create Date: 2026-08-11 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f063_merge_v1_11_4_heads"
down_revision: str | Sequence[str] | None = (
    "f061_default_tenant_timezone",
    "f062_tool_execution_identity",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both additive migration branches without extra DDL."""


def downgrade() -> None:
    """Split back to the two parent heads without extra DDL."""
