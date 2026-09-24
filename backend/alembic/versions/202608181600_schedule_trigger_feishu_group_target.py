"""Add Feishu group delivery targets to schedules and triggers.

Revision ID: f065_feishu_group_target
Revises: f064_tool_call_tenants
Create Date: 2026-08-18 16:00:00

Background: initial_schema creates tables from current ORM metadata, so fresh
databases already have these columns before this revision runs.
Scope: nullable UUID delivery targets on schedules and triggers; DDL only.
Idempotent: inspect each table independently for additions and removals. Keep
the published revision identifiers and existing target values unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f065_feishu_group_target"
down_revision: str | Sequence[str] | None = "f064_tool_call_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_delivery_target(table: str) -> bool:
    return any(
        column["name"] == "delivery_target_id"
        for column in sa.inspect(op.get_bind()).get_columns(table)
    )


def upgrade() -> None:
    for table in ("agent_schedules", "agent_triggers"):
        if not _has_delivery_target(table):
            op.add_column(
                table,
                sa.Column("delivery_target_id", postgresql.UUID(as_uuid=True), nullable=True),
            )


def downgrade() -> None:
    for table in ("agent_triggers", "agent_schedules"):
        if _has_delivery_target(table):
            op.drop_column(table, "delivery_target_id")
