"""Allow distinct delivery receipts from one Runtime checkpoint.

Revision ID: allow_checkpoint_deliveries
Revises: add_agent_model_deleted_at
Create Date: 2026-07-24 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "allow_checkpoint_deliveries"
down_revision: str | None = "add_agent_model_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "agent_run_events"
OLD_CONSTRAINT = "uq_agent_run_events_checkpoint_type"
NON_DELIVERY_INDEX = "uq_agent_run_events_checkpoint_type_non_delivery"


def _inspector():
    return sa.inspect(op.get_bind())


def _constraint_exists(name: str) -> bool:
    return name in {constraint["name"] for constraint in _inspector().get_unique_constraints(TABLE)}


def _index_exists(name: str) -> bool:
    return name in {index["name"] for index in _inspector().get_indexes(TABLE)}


def upgrade() -> None:
    if _constraint_exists(OLD_CONSTRAINT):
        op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")

    if not _index_exists(NON_DELIVERY_INDEX):
        op.create_index(
            NON_DELIVERY_INDEX,
            TABLE,
            ["run_id", "source_checkpoint_id", "event_type"],
            unique=True,
            postgresql_where=sa.text("event_type NOT IN ('delivery_succeeded', 'delivery_failed')"),
        )


def downgrade() -> None:
    if _index_exists(NON_DELIVERY_INDEX):
        op.drop_index(NON_DELIVERY_INDEX, table_name=TABLE)

    if not _constraint_exists(OLD_CONSTRAINT):
        op.create_unique_constraint(
            OLD_CONSTRAINT,
            TABLE,
            ["run_id", "source_checkpoint_id", "event_type"],
        )
