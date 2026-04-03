"""Add wechat to channel_type_enum

Revision ID: add_wechat_channel
Revises:
Create Date: 2024-04-03

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'add_wechat_channel'
down_revision = 'be48e94fa052'  # add_name_translit_fields_to_orgmember
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'wechat' to channel_type_enum
    # PostgreSQL requires ALTER TYPE for enum modifications
    op.execute("""
        ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'wechat';
    """)


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type, which is complex
    # For safety, we leave the value in place
    pass
