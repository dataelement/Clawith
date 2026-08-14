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


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # Fresh deployments build the current ORM schema in ``initial_schema``, so
    # these objects may already exist before Alembic reaches this revision.
    columns = {column["name"] for column in inspector.get_columns("enterprise_info")}
    if "tenant_id" not in columns:
        op.add_column(
            "enterprise_info",
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    indexes = {index["name"] for index in inspector.get_indexes("enterprise_info")}
    if op.f("ix_enterprise_info_tenant_id") not in indexes:
        op.create_index(
            op.f("ix_enterprise_info_tenant_id"),
            "enterprise_info",
            ["tenant_id"],
            unique=False,
        )

    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("enterprise_info")
    }
    if "enterprise_info_info_type_key" in constraints:
        op.drop_constraint(
            "enterprise_info_info_type_key",
            "enterprise_info",
            type_="unique",
        )
    if "uq_enterprise_info_tenant_type" not in constraints:
        op.create_unique_constraint(
            "uq_enterprise_info_tenant_type",
            "enterprise_info",
            ["tenant_id", "info_type"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("enterprise_info")
    }
    if "uq_enterprise_info_tenant_type" in constraints:
        op.drop_constraint(
            "uq_enterprise_info_tenant_type",
            "enterprise_info",
            type_="unique",
        )
    if "enterprise_info_info_type_key" not in constraints:
        op.create_unique_constraint(
            "enterprise_info_info_type_key",
            "enterprise_info",
            ["info_type"],
        )

    indexes = {index["name"] for index in inspector.get_indexes("enterprise_info")}
    if op.f("ix_enterprise_info_tenant_id") in indexes:
        op.drop_index(op.f("ix_enterprise_info_tenant_id"), table_name="enterprise_info")

    columns = {column["name"] for column in inspector.get_columns("enterprise_info")}
    if "tenant_id" in columns:
        op.drop_column("enterprise_info", "tenant_id")
