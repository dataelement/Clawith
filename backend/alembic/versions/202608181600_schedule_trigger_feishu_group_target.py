"""Add Feishu group delivery targets to schedules and triggers.

Revision ID: f065_feishu_group_target
Revises: f064_tool_call_tenants
Create Date: 2026-08-18 16:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f065_feishu_group_target"
down_revision: str | Sequence[str] | None = "f064_tool_call_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_schedules",
        sa.Column("delivery_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_triggers",
        sa.Column("delivery_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_triggers", "delivery_target_id")
    op.drop_column("agent_schedules", "delivery_target_id")
