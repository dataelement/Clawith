"""Add wechat to channel_type_enum.

Revision ID: add_wechat_channel_support
Revises: 440261f5594f
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "add_wechat_channel_support"
down_revision: Union[str, None] = "440261f5594f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'wechat'")
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'whatsapp'")

def downgrade() -> None:
    # PostgreSQL enums cannot drop values safely in place.
    pass
