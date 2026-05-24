"""add whatsapp channel support

Revision ID: add_whatsapp_channel_support
Revises: add_wechat_channel_support
Create Date: 2026-04-16 21:10:00.000000
"""

from alembic import op


revision = "add_whatsapp_channel_support"
down_revision = "add_wechat_channel_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'whatsapp'")


def downgrade() -> None:
    pass
