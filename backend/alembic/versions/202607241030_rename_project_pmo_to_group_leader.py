"""Rename the initial PMO fields to dynamic project-group leader fields.

Revision ID: rename_project_pmo_to_group_leader
Revises: add_project_workflows
Create Date: 2026-07-24 10:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "project_group_leader_rename"
down_revision: str | None = "add_project_workflows"
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
    workflow_columns = _columns("project_workflows")
    if "pmo_agent_id" in workflow_columns and "group_leader_agent_id" not in workflow_columns:
        op.alter_column("project_workflows", "pmo_agent_id", new_column_name="group_leader_agent_id")
    workflow_foreign_keys = _foreign_keys("project_workflows")
    if "fk_project_workflows_pmo_agent_id_agents" in workflow_foreign_keys:
        op.execute(
            "ALTER TABLE project_workflows RENAME CONSTRAINT "
            "fk_project_workflows_pmo_agent_id_agents "
            "TO fk_project_workflows_group_leader_agent_id_agents"
        )

    member_columns = _columns("project_workflow_members")
    if "is_pmo" in member_columns and "is_group_leader" not in member_columns:
        op.alter_column("project_workflow_members", "is_pmo", new_column_name="is_group_leader")


def downgrade() -> None:
    member_columns = _columns("project_workflow_members")
    if "is_group_leader" in member_columns and "is_pmo" not in member_columns:
        op.alter_column("project_workflow_members", "is_group_leader", new_column_name="is_pmo")

    workflow_foreign_keys = _foreign_keys("project_workflows")
    if "fk_project_workflows_group_leader_agent_id_agents" in workflow_foreign_keys:
        op.execute(
            "ALTER TABLE project_workflows RENAME CONSTRAINT "
            "fk_project_workflows_group_leader_agent_id_agents "
            "TO fk_project_workflows_pmo_agent_id_agents"
        )
    workflow_columns = _columns("project_workflows")
    if "group_leader_agent_id" in workflow_columns and "pmo_agent_id" not in workflow_columns:
        op.alter_column("project_workflows", "group_leader_agent_id", new_column_name="pmo_agent_id")
