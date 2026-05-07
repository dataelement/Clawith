"""Add Project Tasks feature: project_tasks / project_task_files tables.

Revision ID: add_project_tasks
Revises: add_projects
Create Date: 2026-04-27

Project Tasks (a.k.a. deliverables) are the per-project to-do list. Distinct
from the agent-scoped `tasks` table which carries supervision / reminder
semantics — this is project-scoped goal tracking that agents can read and
mutate via four new tools: list / create / update / link_file.

All DDL uses IF NOT EXISTS for idempotency.
"""
from alembic import op


revision = "add_project_tasks"
down_revision = "add_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- project_tasks -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_tasks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title               VARCHAR(300) NOT NULL,
            description         TEXT NOT NULL DEFAULT '',
            status              VARCHAR(20)  NOT NULL DEFAULT 'todo',
            assigned_agent_id   UUID REFERENCES agents(id) ON DELETE SET NULL,
            assigned_user_id    UUID REFERENCES users(id)  ON DELETE SET NULL,
            due_date            TIMESTAMPTZ,
            created_by          UUID NOT NULL REFERENCES users(id),
            created_by_type     VARCHAR(10)  NOT NULL DEFAULT 'user',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_tasks_project_status "
        "ON project_tasks (project_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_tasks_project_assigned_agent "
        "ON project_tasks (project_id, assigned_agent_id)"
    )

    # --- project_task_files (M:N junction) --------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_task_files (
            project_task_id  UUID NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
            project_file_id  UUID NOT NULL REFERENCES project_files(id) ON DELETE CASCADE,
            linked_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            linked_by_type   VARCHAR(10) NOT NULL,
            linked_by        UUID NOT NULL,
            PRIMARY KEY (project_task_id, project_file_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_task_files_file_id "
        "ON project_task_files (project_file_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_task_files")
    op.execute("DROP TABLE IF EXISTS project_tasks")
