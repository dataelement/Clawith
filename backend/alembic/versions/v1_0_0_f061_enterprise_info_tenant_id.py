"""Add tenant_id and composite unique constraint to enterprise_info.

Background:
  EnterpriseInfo currently lacks a tenant_id column, causing multi-tenant data bleed where an update
  from one tenant administrator overwrote global EnterpriseInfo entries and pushed synced files to all running agents across tenants.

Scope:
  Add tenant_id UUID column (indexed) to enterprise_info.
  Drop legacy single info_type unique constraint.
  Add composite unique constraint uq_enterprise_info_tenant_type on (tenant_id, info_type).

Idempotence:
  Safe for retry. Pure DDL migration without blocking data locks.

Revision ID: f061_enterprise_info_tenant_id
Revises: f060_tenant_id_backfill
Create Date: 2026-08-06 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f061_enterprise_info_tenant_id"
down_revision: Union[str, None] = "f060_tenant_id_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("enterprise_info")
    }


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("enterprise_info")
    }


def _unique_constraint_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints("enterprise_info")
        if constraint["name"]
    }


def upgrade() -> None:
    # 1. Add tenant_id column with default uuid generator or nullable first if populated
    if "tenant_id" not in _column_names():
        op.add_column("enterprise_info", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    if "ix_enterprise_info_tenant_id" not in _index_names():
        op.create_index(op.f("ix_enterprise_info_tenant_id"), "enterprise_info", ["tenant_id"], unique=False)

    # 2. Drop legacy single info_type unique constraint
    if "enterprise_info_info_type_key" in _unique_constraint_names():
        op.drop_constraint("enterprise_info_info_type_key", "enterprise_info", type_="unique")

    # 3. Create new composite unique constraint (tenant_id, info_type)
    if "uq_enterprise_info_tenant_type" not in _unique_constraint_names():
        op.create_unique_constraint("uq_enterprise_info_tenant_type", "enterprise_info", ["tenant_id", "info_type"])


def downgrade() -> None:
    if "uq_enterprise_info_tenant_type" in _unique_constraint_names():
        op.drop_constraint("uq_enterprise_info_tenant_type", "enterprise_info", type_="unique")
    if "enterprise_info_info_type_key" not in _unique_constraint_names():
        op.create_unique_constraint("enterprise_info_info_type_key", "enterprise_info", ["info_type"])
    if "ix_enterprise_info_tenant_id" in _index_names():
        op.drop_index(op.f("ix_enterprise_info_tenant_id"), table_name="enterprise_info")
    if "tenant_id" in _column_names():
        op.drop_column("enterprise_info", "tenant_id")
