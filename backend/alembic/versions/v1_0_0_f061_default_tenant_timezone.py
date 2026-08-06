"""F061: Use Beijing as the required default tenant timezone.

Revision ID: f061_default_tenant_timezone
Revises: f060_tenant_id_backfill
Create Date: 2026-08-05 12:00:00

Background:
  Agent scheduling inherits its timezone from the Tenant when the Agent has no
  override, so new Tenants need a stable platform default.

Scope:
  Require the Tenant timezone column and change its server default to
  Asia/Shanghai.

Idempotent:
  Reapplying the same nullability and server-default metadata is safe.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "f061_default_tenant_timezone"
down_revision: str | None = "f060_tenant_id_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "timezone",
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="Asia/Shanghai",
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "timezone",
        existing_type=sa.String(length=50),
        nullable=True,
        server_default="UTC",
    )
