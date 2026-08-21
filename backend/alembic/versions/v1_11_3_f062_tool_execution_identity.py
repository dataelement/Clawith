"""Separate provider correlation from Runtime Tool Call identity.

Background:
  Provider-local Tool Call IDs can repeat across Assistant turns, while the
  Runtime Receipt requires a Run-local stable Call Instance identity.

Scope:
  Add nullable provider_call_id and contract_version columns to
  agent_tool_executions. Existing rows remain valid without a data backfill.

Idempotent:
  Each nullable column is added or removed only when its current schema state
  requires the DDL operation.

Revision ID: f062_tool_execution_identity
Revises: f061_enterprise_info_tenant_id
Create Date: 2026-08-10 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f062_tool_execution_identity"
down_revision: str | None = "f061_enterprise_info_tenant_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "agent_tool_executions"
PROVIDER_CALL_ID = "provider_call_id"
CONTRACT_VERSION = "contract_version"


def _column_names(*, offline_default: set[str]) -> set[str]:
    try:
        inspector = sa.inspect(op.get_bind())
    except sa.exc.NoInspectionAvailable:
        return offline_default
    return {
        str(column["name"])
        for column in inspector.get_columns(TABLE_NAME)
    }


def upgrade() -> None:
    columns = _column_names(offline_default=set())
    if PROVIDER_CALL_ID not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column(PROVIDER_CALL_ID, sa.String(length=255), nullable=True),
        )
    if CONTRACT_VERSION not in columns:
        op.add_column(
            TABLE_NAME,
            sa.Column(CONTRACT_VERSION, sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names(
        offline_default={PROVIDER_CALL_ID, CONTRACT_VERSION}
    )
    if CONTRACT_VERSION in columns:
        op.drop_column(TABLE_NAME, CONTRACT_VERSION)
    if PROVIDER_CALL_ID in columns:
        op.drop_column(TABLE_NAME, PROVIDER_CALL_ID)
