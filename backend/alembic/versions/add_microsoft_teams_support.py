"""Add Microsoft Teams support to im_provider and channel_type enums."""

from alembic import op
import sqlalchemy as sa

revision = "add_microsoft_teams_support"
down_revision = "add_agent_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'microsoft_teams' to im_provider_enum
    op.execute("ALTER TYPE im_provider_enum ADD VALUE IF NOT EXISTS 'microsoft_teams'")
    op.add_column('chat_messages', sa.Column('thinking', sa.Text(), nullable=True))
    # Add 'microsoft_teams' to channel_type_enum
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'microsoft_teams'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    op.drop_column('chat_messages', 'thinking')
    pass
