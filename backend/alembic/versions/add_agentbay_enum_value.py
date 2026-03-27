"""Add agentbay and atlassian to channel_type_enum.

Revision ID: add_agentbay_enum_value
Revises: add_llm_max_output_tokens
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'add_agentbay_enum_value'
down_revision = 'add_llm_max_output_tokens'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adding enum values must be done outside a transaction block in PostgreSQL.
    # We use IF NOT EXISTS for idempotence.
    # Note: Alembic's default behavior is to wrap in a transaction, but ALTER TYPE ... ADD VALUE
    # is allowed since PostgreSQL 12 if not already in a transaction.
    # We'll use op.get_bind().execution_options(isolation_level="AUTOCOMMIT") if possible, 
    # but op.execute should work if the dialect supports it.
    
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'atlassian'")
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'agentbay'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass
