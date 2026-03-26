"""Add source column to users table."""

from alembic import op
import sqlalchemy as sa

revision = "add_user_source"
down_revision = "add_llm_max_output_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("source", sa.String(50), nullable=True, server_default="web"))
    op.create_index("ix_users_source", "users", ["source"])


def downgrade():
    op.drop_index("ix_users_source", "users")
    op.drop_column("users", "source")
