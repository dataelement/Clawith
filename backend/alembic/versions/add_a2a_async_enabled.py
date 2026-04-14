"""Add a2a_async_enabled column to tenants table.

Revision ID: f1a2b3c4d5e6
Revises: d9cbd43b62e5
Create Date: 2026-04-10 02:50:00.000000
"""
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "d9cbd43b62e5"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents DROP COLUMN IF EXISTS a2a_async_enabled"
    )
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS a2a_async_enabled BOOLEAN DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS a2a_async_enabled")
