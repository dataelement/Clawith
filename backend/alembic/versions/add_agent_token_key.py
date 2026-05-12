"""Add token_key fields to agents table for Agent API calling.

Revision ID: add_agent_token_key
Revises: None (standalone — depends on current head)
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "add_agent_token_key"
down_revision = None  # Will be set by Alembic chain
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("token_key", sa.String(128), nullable=True, index=True))
    op.add_column("agents", sa.Column("token_key_suffix", sa.String(4), nullable=True))
    # Create index explicitly for the token_key lookup
    op.create_index("ix_agents_token_key", "agents", ["token_key"], unique=False)


def downgrade():
    op.drop_index("ix_agents_token_key", table_name="agents")
    op.drop_column("agents", "token_key_suffix")
    op.drop_column("agents", "token_key")
