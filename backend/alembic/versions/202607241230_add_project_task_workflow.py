"""Add task-driven project group workflow fields.

Revision ID: add_project_task_workflow
Revises: project_group_leader_rename
Create Date: 2026-07-24 12:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_project_task_workflow"
down_revision: str | None = "project_group_leader_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if foreign_key.get("name")
    }


def upgrade() -> None:
    # PostgreSQL enum changes are additive and must be committed before values
    # are used. Alembic executes each statement outside a transaction here.
    op.execute("ALTER TYPE task_status_enum ADD VALUE IF NOT EXISTS 'blocked'")
    op.execute("ALTER TYPE task_status_enum ADD VALUE IF NOT EXISTS 'failed'")
    existing = _columns("tasks")
    additions = (
        ("project_workflow_id", postgresql.UUID(as_uuid=True)),
        ("group_id", postgresql.UUID(as_uuid=True)),
        ("session_id", postgresql.UUID(as_uuid=True)),
        ("trigger_message_id", postgresql.UUID(as_uuid=True)),
        ("dependency_task_ids", postgresql.JSONB(), sa.text("'[]'::jsonb")),
        ("report_to_agent_id", postgresql.UUID(as_uuid=True)),
        ("is_project_closure", sa.Boolean(), sa.text("false")),
    )
    for item in additions:
        name, kind, *default = item
        if name not in existing:
            op.add_column(
                "tasks",
                sa.Column(name, kind, nullable=False if default else True, server_default=default[0] if default else None),
            )
    foreign_keys = _foreign_keys("tasks")
    for name, column, target, ondelete in (
        ("fk_tasks_project_workflow_id_project_workflows", "project_workflow_id", "project_workflows", "CASCADE"),
        ("fk_tasks_group_id_groups", "group_id", "groups", "CASCADE"),
        ("fk_tasks_session_id_chat_sessions", "session_id", "chat_sessions", "SET NULL"),
        ("fk_tasks_trigger_message_id_chat_messages", "trigger_message_id", "chat_messages", "SET NULL"),
        ("fk_tasks_report_to_agent_id_agents", "report_to_agent_id", "agents", "SET NULL"),
    ):
        if name not in foreign_keys:
            op.create_foreign_key(name, "tasks", target, [column], ["id"], ondelete=ondelete)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("tasks")}
    if "ix_tasks_project_workflow_status" not in indexes:
        op.create_index("ix_tasks_project_workflow_status", "tasks", ["project_workflow_id", "status"])
    if "ix_tasks_trigger_message_id" not in indexes:
        op.create_index("ix_tasks_trigger_message_id", "tasks", ["trigger_message_id"])


def downgrade() -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("tasks")}
    for name in ("ix_tasks_trigger_message_id", "ix_tasks_project_workflow_status"):
        if name in indexes:
            op.drop_index(name, table_name="tasks")
    foreign_keys = _foreign_keys("tasks")
    for name in (
        "fk_tasks_report_to_agent_id_agents",
        "fk_tasks_trigger_message_id_chat_messages",
        "fk_tasks_session_id_chat_sessions",
        "fk_tasks_group_id_groups",
        "fk_tasks_project_workflow_id_project_workflows",
    ):
        if name in foreign_keys:
            op.drop_constraint(name, "tasks", type_="foreignkey")
    for name in (
        "is_project_closure",
        "report_to_agent_id",
        "dependency_task_ids",
        "trigger_message_id",
        "session_id",
        "group_id",
        "project_workflow_id",
    ):
        if name in _columns("tasks"):
            op.drop_column("tasks", name)
