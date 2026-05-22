"""Add tenant_settings table for tenant-scoped tool and company config.

Revision ID: add_tenant_settings
Revises: add_notifications_table
Create Date: 2026-05-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_tenant_settings"
down_revision: Union[str, Sequence[str], None] = "add_notifications_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if not conn.dialect.has_table(conn, "tenant_settings"):
        op.create_table(
            "tenant_settings",
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("key", sa.String(length=100), primary_key=True, nullable=False),
            sa.Column(
                "value",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_table("tenant_settings")
