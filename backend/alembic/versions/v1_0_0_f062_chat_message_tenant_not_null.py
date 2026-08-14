"""Require tenant ownership for every chat message.

Background:
  ChatMessage is tenant-scoped, but its tenant_id column remained nullable after
  the original backfill. Missing ownership therefore produced messages that
  were committed successfully and then hidden by automatic tenant filtering.

Scope:
  Make chat_messages.tenant_id NOT NULL after all writers have been updated to
  provide an explicit tenant.

Idempotent:
  Inspector checks the current nullability before changing the column. Existing
  NULL rows intentionally stop the migration so operators can reconcile their
  ownership out of band instead of assigning data to the wrong tenant.

Revision ID: f062_chat_message_tenant_nn
Revises: f061_enterprise_info_tenant_id
Create Date: 2026-08-14 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f062_chat_message_tenant_nn"
down_revision: str | None = "f061_enterprise_info_tenant_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_column() -> dict:
    inspector = sa.inspect(op.get_bind())
    return next(
        column
        for column in inspector.get_columns("chat_messages")
        if column["name"] == "tenant_id"
    )


def upgrade() -> None:
    if _tenant_column()["nullable"]:
        op.alter_column(
            "chat_messages",
            "tenant_id",
            existing_type=sa.UUID(as_uuid=True),
            nullable=False,
        )


def downgrade() -> None:
    if not _tenant_column()["nullable"]:
        op.alter_column(
            "chat_messages",
            "tenant_id",
            existing_type=sa.UUID(as_uuid=True),
            nullable=True,
        )
