"""Project Files: switch unique constraint from filename to physical_path.

Revision ID: project_files_path_aware
Revises: add_project_tasks
Create Date: 2026-04-28

With layered/subdirectory support, two files can share the same display name
in different folders (e.g. "notes.md" at root vs "posts/notes.md"). The old
unique constraint (project_id, filename) blocked that. This migration drops
it and adds (project_id, physical_path) instead.

For existing rows physical_path == filename, so the new constraint is
already satisfied — no data backfill needed.
"""
from alembic import op


revision = "project_files_path_aware"
down_revision = "add_project_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE project_files DROP CONSTRAINT IF EXISTS uq_project_files_project_filename")
    op.execute(
        "ALTER TABLE project_files "
        "ADD CONSTRAINT uq_project_files_project_path UNIQUE (project_id, physical_path)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE project_files DROP CONSTRAINT IF EXISTS uq_project_files_project_path")
    op.execute(
        "ALTER TABLE project_files "
        "ADD CONSTRAINT uq_project_files_project_filename UNIQUE (project_id, filename)"
    )
