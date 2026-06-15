"""Add external_http channel type.

Revision ID: add_external_http_channel
Revises: add_title_to_agent_focus_items
Create Date: 2026-06-08
"""

from typing import Sequence, Union

from alembic import op


revision: str = "add_external_http_channel"
down_revision: Union[str, Sequence[str], None] = "add_title_to_agent_focus_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'external_http'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum values in place.
    pass
