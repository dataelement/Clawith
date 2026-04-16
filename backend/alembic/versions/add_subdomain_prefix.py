"""Add subdomain_prefix to tenants table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "add_subdomain_prefix"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("tenants")]
    if "subdomain_prefix" not in columns:
        op.add_column("tenants", sa.Column("subdomain_prefix", sa.String(50), nullable=True))
    indexes = [i["name"] for i in inspector.get_indexes("tenants")]
    if "ix_tenants_subdomain_prefix" not in indexes:
        op.create_index("ix_tenants_subdomain_prefix", "tenants", ["subdomain_prefix"], unique=True)

def downgrade() -> None:
    op.drop_index("ix_tenants_subdomain_prefix", "tenants")
    op.drop_column("tenants", "subdomain_prefix")
