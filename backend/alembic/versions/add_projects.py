"""Add Project feature: projects / project_agents / project_files tables
and chat_sessions.project_id column.

Revision ID: add_projects
Revises: add_agent_api_key
Create Date: 2026-04-23

Project is a shared workspace container for multiple agents collaborating
on the same goal — distinct from an agent's private workspace.

All DDL uses IF NOT EXISTS for idempotency.
"""
from alembic import op


revision = "add_projects"
down_revision = "add_agent_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- projects ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name             VARCHAR(200) NOT NULL,
            description      VARCHAR(500) NOT NULL DEFAULT '',
            created_by       UUID NOT NULL REFERENCES users(id),
            scope_type       VARCHAR(20) NOT NULL DEFAULT 'tenant',
            scope_id         UUID NOT NULL,
            chat_visibility  VARCHAR(20) NOT NULL DEFAULT 'shared',
            archived_at      TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_scope_name "
        "ON projects (scope_type, scope_id, name)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_scope ON projects (scope_type, scope_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_created_by ON projects (created_by)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_archived_at ON projects (archived_at)")

    # --- project_agents (M2M) ---------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_agents (
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            agent_id    UUID NOT NULL REFERENCES agents(id)   ON DELETE CASCADE,
            added_by    UUID NOT NULL REFERENCES users(id),
            added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (project_id, agent_id)
        )
        """
    )
    # Reverse-lookup index: given an agent, find all its projects
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_agents_agent_id ON project_agents (agent_id)")

    # --- project_files (metadata — physical bytes live on disk) -----------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_files (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            filename         VARCHAR(255) NOT NULL,
            physical_path    VARCHAR(500) NOT NULL,
            size_bytes       BIGINT NOT NULL,
            mime_type        VARCHAR(100) NOT NULL DEFAULT '',
            created_by_type  VARCHAR(10)  NOT NULL,
            created_by       UUID NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_files_project_id ON project_files (project_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_files_project_filename "
        "ON project_files (project_id, filename)"
    )

    # --- chat_sessions.project_id (nullable, SET NULL on project delete) --
    op.execute(
        """
        ALTER TABLE chat_sessions
          ADD COLUMN IF NOT EXISTS project_id UUID
          REFERENCES projects(id) ON DELETE SET NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_project_id "
        "ON chat_sessions (project_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS project_id")
    op.execute("DROP TABLE IF EXISTS project_files")
    op.execute("DROP TABLE IF EXISTS project_agents")
    op.execute("DROP TABLE IF EXISTS projects")
