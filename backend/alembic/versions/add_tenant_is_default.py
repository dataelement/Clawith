"""Add is_default field to tenants table.

Revision ID: add_tenant_is_default
Revises: add_subdomain_prefix
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "add_tenant_is_default"
down_revision = "add_subdomain_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add is_default column (idempotent)
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('tenants')]
    if 'is_default' not in cols:
        op.add_column('tenants', sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'))

    # 2. Set the earliest active tenant as default (only if no tenant is already default)
    conn.execute(sa.text("""
        UPDATE tenants
        SET is_default = true
        WHERE id = (
            SELECT id FROM tenants WHERE is_active = true ORDER BY created_at ASC LIMIT 1
        )
        AND NOT EXISTS (SELECT 1 FROM tenants WHERE is_default = true)
    """))


def downgrade() -> None:
    op.drop_column('tenants', 'is_default')
