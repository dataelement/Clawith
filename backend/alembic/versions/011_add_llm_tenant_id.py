"""Add tenant_id to llm_models table for per-company model pools.

Revision ID: add_llm_tenant_id
Revises: 20260313_column_modify
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "add_llm_tenant_id"
down_revision = "20260313_column_modify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tenant_id column (nullable so existing rows are unaffected)
    op.execute("ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_llm_models_tenant_id ON llm_models (tenant_id)")

    # Assign existing models to the first tenant (Demo Company) so they remain visible
    op.execute("""
        UPDATE llm_models
        SET tenant_id = (SELECT id FROM tenants ORDER BY created_at ASC LIMIT 1)
        WHERE tenant_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_models_tenant_id")
    op.execute("ALTER TABLE llm_models DROP COLUMN IF EXISTS tenant_id")
