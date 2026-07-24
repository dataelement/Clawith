"""Add leader-led project workflow and project group ownership schema.

Revision ID: add_project_workflows
Revises: add_agent_model_deleted_at
Create Date: 2026-07-24 09:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_project_workflows"
down_revision: str | None = "add_agent_model_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table)}


def _table_exists(table: str) -> bool:
    return table in _inspector().get_table_names()


def _foreign_key_exists(table: str, constrained_columns: list[str]) -> bool:
    return any(
        foreign_key.get("constrained_columns") == constrained_columns
        for foreign_key in _inspector().get_foreign_keys(table)
    )


def upgrade() -> None:
    if "owner_agent_id" not in _columns("groups"):
        op.add_column("groups", sa.Column("owner_agent_id", postgresql.UUID(as_uuid=True), nullable=True))
    if not _foreign_key_exists("groups", ["owner_agent_id"]):
        op.create_foreign_key(
            "fk_groups_owner_agent_id_agents", "groups", "agents", ["owner_agent_id"], ["id"], ondelete="SET NULL"
        )

    # Existing native-group constraint has a stable name in this project.
    # Recreate it to allow the group-leader owner membership without changing
    # human manager permissions.
    constraints = {item.get("name") for item in _inspector().get_check_constraints("group_members")}
    if "ck_group_members_role" in constraints:
        op.drop_constraint("ck_group_members_role", "group_members", type_="check")
    op.create_check_constraint(
        "ck_group_members_role", "group_members", "role IN ('manager', 'owner', 'member')"
    )

    if not _table_exists("project_workflows"):
        op.create_table(
            "project_workflows",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("template_key", sa.String(length=64), nullable=False),
            sa.Column("requirements", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
            sa.Column("team_plan", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("group_leader_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_project_workflows_tenant_id_tenants", ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["creator_id"], ["users.id"], name="fk_project_workflows_creator_id_users"),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name="fk_project_workflows_group_id_groups"),
            sa.ForeignKeyConstraint(["group_leader_agent_id"], ["agents.id"], name="fk_project_workflows_group_leader_agent_id_agents"),
        )
    indexes = {item["name"] for item in _inspector().get_indexes("project_workflows")}
    if "ix_project_workflows_tenant_created_at" not in indexes:
        op.create_index("ix_project_workflows_tenant_created_at", "project_workflows", ["tenant_id", "created_at"])

    if not _table_exists("project_workflow_members"):
        op.create_table(
            "project_workflow_members",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("role_key", sa.String(length=64), nullable=False),
            sa.Column("role_title", sa.String(length=100), nullable=False),
            sa.Column("is_group_leader", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["workflow_id"], ["project_workflows.id"], name="fk_project_workflow_members_workflow_id", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_project_workflow_members_agent_id_agents"),
        )
    indexes = {item["name"] for item in _inspector().get_indexes("project_workflow_members")}
    if "ix_project_workflow_members_workflow_id" not in indexes:
        op.create_index("ix_project_workflow_members_workflow_id", "project_workflow_members", ["workflow_id"])


def downgrade() -> None:
    if _table_exists("project_workflow_members"):
        op.drop_table("project_workflow_members")
    if _table_exists("project_workflows"):
        op.drop_table("project_workflows")
    constraints = {item.get("name") for item in _inspector().get_check_constraints("group_members")}
    if "ck_group_members_role" in constraints:
        op.drop_constraint("ck_group_members_role", "group_members", type_="check")
    op.create_check_constraint("ck_group_members_role", "group_members", "role IN ('manager', 'member')")
    if _foreign_key_exists("groups", ["owner_agent_id"]):
        op.drop_constraint("fk_groups_owner_agent_id_agents", "groups", type_="foreignkey")
    if "owner_agent_id" in _columns("groups"):
        op.drop_column("groups", "owner_agent_id")
