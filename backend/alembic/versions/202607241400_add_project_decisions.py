"""Add project-group user decision inbox.

Revision ID: add_project_decisions
Revises: add_project_task_workflow
Create Date: 2026-07-24 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_project_decisions"
down_revision: str | None = "add_project_task_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "project_decisions" not in inspector.get_table_names():
        op.create_table(
            "project_decisions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("requesting_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("context", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("response", sa.Text(), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["workflow_id"], ["project_workflows.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["requesting_agent_id"], ["agents.id"], ondelete="SET NULL"),
        )
    indexes = {index["name"] for index in inspector.get_indexes("project_decisions")}
    if "ix_project_decisions_group_status" not in indexes:
        op.create_index("ix_project_decisions_group_status", "project_decisions", ["group_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_decisions_group_status", table_name="project_decisions")
    op.drop_table("project_decisions")
