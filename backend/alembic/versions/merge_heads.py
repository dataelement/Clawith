"""Merge upstream and local migration heads."""

from alembic import op

revision = "merge_upstream_and_local"
down_revision = ("add_daily_token_usage", "add_user_source")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
