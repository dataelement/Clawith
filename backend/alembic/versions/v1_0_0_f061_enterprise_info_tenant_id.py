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

# revision identifiers, used by Alembic.
revision: str = "f061_enterprise_info_tenant_id"
down_revision: Union[str, None] = "f060_tenant_id_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This migration must be idempotent with respect to a freshly created schema.
    # 001_initial_schema.py builds the schema with Base.metadata.create_all(), which
    # already produces enterprise_info.tenant_id because the model declares it, so an
    # unconditional op.add_column() fails on every fresh deployment and
    # `alembic upgrade head` cannot complete.
    #
    # The IF NOT EXISTS / IF EXISTS guards match the convention already used in
    # 010_column_modify.py, and make the module docstring's "Safe for retry" claim hold.

    # 1. Add tenant_id column with default uuid generator or nullable first if populated
    op.execute("ALTER TABLE enterprise_info ADD COLUMN IF NOT EXISTS tenant_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_enterprise_info_tenant_id ON enterprise_info (tenant_id)"
    )

    # 2. Drop legacy single info_type unique constraint
    op.execute("ALTER TABLE enterprise_info DROP CONSTRAINT IF EXISTS enterprise_info_info_type_key")

    # 3. Create new composite unique constraint (tenant_id, info_type)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_enterprise_info_tenant_type'
            ) THEN
                ALTER TABLE enterprise_info
                    ADD CONSTRAINT uq_enterprise_info_tenant_type UNIQUE (tenant_id, info_type);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_enterprise_info_tenant_type", "enterprise_info", type_="unique")
    op.create_unique_constraint("enterprise_info_info_type_key", "enterprise_info", ["info_type"])
    op.drop_index(op.f("ix_enterprise_info_tenant_id"), table_name="enterprise_info")
    op.drop_column("enterprise_info", "tenant_id")
