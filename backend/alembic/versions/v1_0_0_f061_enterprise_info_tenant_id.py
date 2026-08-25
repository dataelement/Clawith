"""Add tenant_id and composite unique constraint to enterprise_info.

Background:
  EnterpriseInfo currently lacks a tenant_id column, causing multi-tenant data bleed where an update
  from one tenant administrator overwrote global EnterpriseInfo entries and pushed synced files to all running agents across tenants.

Scope:
  Add tenant_id UUID column (indexed) to enterprise_info.
  Drop legacy single info_type unique constraint.
  Add composite unique constraint uq_enterprise_info_tenant_type on (tenant_id, info_type).

Idempotence:
  Safe for retry and for fresh installs whose initial metadata already contains
  the target tenant-scoped shape.

Revision ID: f061_enterprise_info_tenant_id
Revises: f060_tenant_id_backfill
Create Date: 2026-08-06 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f061_enterprise_info_tenant_id"
down_revision: str | None = "f060_tenant_id_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "enterprise_info"
TENANT_COLUMN = "tenant_id"
TENANT_INDEX = "ix_enterprise_info_tenant_id"
LEGACY_UNIQUE = "enterprise_info_info_type_key"
TENANT_UNIQUE = "uq_enterprise_info_tenant_type"


def _schema_names(
    *,
    offline_default: tuple[set[str], set[str], set[str]],
) -> tuple[set[str], set[str], set[str]]:
    """Return column, index, and unique-constraint names for the target table."""
    try:
        inspector = sa.inspect(op.get_bind())
    except sa.exc.NoInspectionAvailable:
        return offline_default

    columns = {str(column["name"]) for column in inspector.get_columns(TABLE_NAME)}
    indexes = {
        str(index["name"])
        for index in inspector.get_indexes(TABLE_NAME)
        if index.get("name")
    }
    unique_constraints = {
        str(constraint["name"])
        for constraint in inspector.get_unique_constraints(TABLE_NAME)
        if constraint.get("name")
    }
    return columns, indexes, unique_constraints


def upgrade() -> None:
    columns, indexes, unique_constraints = _schema_names(
        offline_default=(set(), set(), {LEGACY_UNIQUE}),
    )

    if TENANT_COLUMN not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column(TENANT_COLUMN, postgresql.UUID(as_uuid=True), nullable=True),
        )
    if TENANT_INDEX not in indexes:
        op.create_index(TENANT_INDEX, TABLE_NAME, [TENANT_COLUMN], unique=False)
    if LEGACY_UNIQUE in unique_constraints:
        op.drop_constraint(LEGACY_UNIQUE, TABLE_NAME, type_="unique")
    if TENANT_UNIQUE not in unique_constraints:
        op.create_unique_constraint(
            TENANT_UNIQUE,
            TABLE_NAME,
            [TENANT_COLUMN, "info_type"],
        )


def downgrade() -> None:
    columns, indexes, unique_constraints = _schema_names(
        offline_default=(
            {TENANT_COLUMN, "info_type"},
            {TENANT_INDEX},
            {TENANT_UNIQUE},
        ),
    )

    if TENANT_UNIQUE in unique_constraints:
        op.drop_constraint(TENANT_UNIQUE, TABLE_NAME, type_="unique")
    if LEGACY_UNIQUE not in unique_constraints:
        op.create_unique_constraint(LEGACY_UNIQUE, TABLE_NAME, ["info_type"])
    if TENANT_INDEX in indexes:
        op.drop_index(TENANT_INDEX, table_name=TABLE_NAME)
    if TENANT_COLUMN in columns:
        op.drop_column(TABLE_NAME, TENANT_COLUMN)
