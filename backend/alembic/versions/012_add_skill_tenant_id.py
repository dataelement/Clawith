"""Add tenant_id to skills table for per-company skill scoping.

Revision ID: add_skill_tenant_id
Revises: add_llm_tenant_id
"""
from alembic import op
import sqlalchemy as sa

revision = "add_skill_tenant_id"
down_revision = "add_llm_tenant_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tenant_id column to skills (nullable — builtin skills have NULL tenant_id)
    op.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_skills_tenant_id ON skills (tenant_id)")

    # Ensure tools table has tenant_id index (column already exists)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tools_tenant_id ON tools (tenant_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_skills_tenant_id")
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_tools_tenant_id")
