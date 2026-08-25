"""F060: Add tenant_id to audit_logs, notifications, tasks, and chat_messages with backfill.

Revision ID: f060_add_tenant_id_missing_tables
Revises: allow_checkpoint_deliveries
Create Date: 2026-08-04

Background:
  Complete multi-tenant isolation migration by introducing automatic tenant filtering
  and backfilling missing tenant_id columns across audit_logs, notifications, tasks,
  and chat_messages.

Scope:
  1. Add tenant_id column and index to audit_logs, notifications, tasks, and chat_messages.
  2. Backfill tenant_id from parent users/agents/chat_sessions via SQL JOINs.
  3. Clean up orphan/dirty data by assigning to default system tenant.

Idempotent:
  Inspector checks column existence before adding columns/indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f060_tenant_id_backfill"
down_revision: str | None = "allow_checkpoint_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add tenant_id columns if missing
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name in ("audit_logs", "notifications", "tasks", "chat_messages"):
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        if "tenant_id" not in columns:
            op.add_column(
                table_name,
                sa.Column(
                    "tenant_id",
                    sa.UUID(as_uuid=True),
                    sa.ForeignKey("tenants.id"),
                    nullable=True,
                ),
            )
            op.create_index(
                f"ix_{table_name}_tenant_id",
                table_name,
                ["tenant_id"],
            )

    # 2. Backfill audit_logs.tenant_id
    op.execute(
        """
        UPDATE audit_logs
        SET tenant_id = users.tenant_id
        FROM users
        WHERE audit_logs.user_id = users.id AND audit_logs.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE audit_logs
        SET tenant_id = agents.tenant_id
        FROM agents
        WHERE audit_logs.agent_id = agents.id AND audit_logs.tenant_id IS NULL;
        """
    )

    # 3. Backfill notifications.tenant_id
    op.execute(
        """
        UPDATE notifications
        SET tenant_id = users.tenant_id
        FROM users
        WHERE notifications.user_id = users.id AND notifications.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE notifications
        SET tenant_id = agents.tenant_id
        FROM agents
        WHERE notifications.agent_id = agents.id AND notifications.tenant_id IS NULL;
        """
    )

    # 4. Backfill tasks.tenant_id
    op.execute(
        """
        UPDATE tasks
        SET tenant_id = agents.tenant_id
        FROM agents
        WHERE tasks.agent_id = agents.id AND tasks.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE tasks
        SET tenant_id = users.tenant_id
        FROM users
        WHERE tasks.created_by = users.id AND tasks.tenant_id IS NULL;
        """
    )

    # 5. Backfill chat_messages.tenant_id
    op.execute(
        """
        UPDATE chat_messages
        SET tenant_id = agents.tenant_id
        FROM agents
        WHERE chat_messages.agent_id = agents.id AND chat_messages.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE chat_messages
        SET tenant_id = users.tenant_id
        FROM users
        WHERE chat_messages.user_id = users.id AND chat_messages.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE chat_messages
        SET tenant_id = chat_sessions.tenant_id
        FROM chat_sessions
        WHERE chat_messages.conversation_id = chat_sessions.id::text AND chat_messages.tenant_id IS NULL;
        """
    )

    # 6. Orphan & dirty data fallback to default system tenant if any records remain NULL
    for table_name in ("audit_logs", "notifications", "tasks", "chat_messages"):
        op.execute(
            f"""
            UPDATE {table_name}
            SET tenant_id = (SELECT id FROM tenants ORDER BY created_at LIMIT 1)
            WHERE tenant_id IS NULL AND EXISTS (SELECT 1 FROM tenants);
            """
        )


def downgrade() -> None:
    for table_name in ("chat_messages", "tasks", "notifications", "audit_logs"):
        op.drop_index(f"ix_{table_name}_tenant_id", table_name=table_name)
        op.drop_column(table_name, "tenant_id")
