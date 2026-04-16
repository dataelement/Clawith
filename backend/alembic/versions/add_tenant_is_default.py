"""Add is_default field to tenants table."""

from alembic import op
import sqlalchemy as sa

revision = "add_tenant_is_default"
down_revision = "add_subdomain_prefix"
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('tenants')]
    if 'is_default' not in cols:
        op.add_column('tenants', sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'))
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
