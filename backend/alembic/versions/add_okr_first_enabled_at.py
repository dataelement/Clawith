"""Add first_enabled_at to okr_settings for stable OKR period history.

Revision ID: add_okr_first_enabled_at
Revises: merge_workspace_and_okr
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_okr_first_enabled_at"
down_revision: Union[str, Sequence[str], None] = "merge_workspace_and_okr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "okr_settings",
        sa.Column("first_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE okr_settings s
        SET first_enabled_at = COALESCE(
            (
                SELECT MIN(o.period_start)::timestamptz
                FROM okr_objectives o
                WHERE o.tenant_id = s.tenant_id
            ),
            NOW()
        )
        WHERE s.enabled = TRUE
          AND s.first_enabled_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("okr_settings", "first_enabled_at")
