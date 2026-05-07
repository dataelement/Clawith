"""Add bridge_attached / bridge_detached to activity_action_enum.

Revision ID: add_bridge_activity_enum
Revises: add_bridge_adapter

session_dispatcher.py logs these two events when a bridge connects
or disconnects, but the enum never included them — so every
attach/detach produced an InvalidTextRepresentationError and the
row was dropped. This backfills the enum values; existing rows
are unaffected.
"""
from alembic import op


revision = 'add_bridge_activity_enum'
down_revision = 'add_bridge_adapter'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE activity_action_enum ADD VALUE IF NOT EXISTS 'bridge_attached'")
    op.execute("ALTER TYPE activity_action_enum ADD VALUE IF NOT EXISTS 'bridge_detached'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass
