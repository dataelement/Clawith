"""Keep Experience edit drafts independent from their published source.

Revision ID: add_experience_revision_drafts
Revises: unify_runtime_group_schema
Create Date: 2026-07-17 15:30:00

The unified migration also contains this final schema for fresh databases. This
follow-up is intentionally idempotent so environments that already applied an
earlier copy of that migration still receive the column, foreign key, and index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_experience_revision_drafts"
down_revision: str | None = "unify_runtime_group_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "experience_entries"
COLUMN = "draft_of_id"
INDEX = "ix_experience_entries_draft_of_id"
FOREIGN_KEY = "fk_experience_entries_draft_of_id"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        op.add_column(
            TABLE,
            sa.Column(COLUMN, postgresql.UUID(as_uuid=True), nullable=True),
        )

    inspector = _inspector()
    foreign_keys = inspector.get_foreign_keys(TABLE)
    if not any(foreign_key.get("constrained_columns") == [COLUMN] for foreign_key in foreign_keys):
        op.create_foreign_key(
            FOREIGN_KEY,
            TABLE,
            TABLE,
            [COLUMN],
            ["id"],
            ondelete="SET NULL",
        )

    inspector = _inspector()
    indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
    if INDEX not in indexes:
        op.create_index(INDEX, TABLE, [COLUMN], unique=False)


def downgrade() -> None:
    inspector = _inspector()
    indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
    if INDEX in indexes:
        op.drop_index(INDEX, table_name=TABLE)

    inspector = _inspector()
    foreign_keys = inspector.get_foreign_keys(TABLE)
    draft_foreign_key = next(
        (
            foreign_key
            for foreign_key in foreign_keys
            if foreign_key.get("constrained_columns") == [COLUMN]
        ),
        None,
    )
    if draft_foreign_key:
        op.drop_constraint(
            draft_foreign_key.get("name") or FOREIGN_KEY,
            TABLE,
            type_="foreignkey",
        )

    inspector = _inspector()
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN in columns:
        op.drop_column(TABLE, COLUMN)
