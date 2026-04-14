"""Add agent_credentials table for storing encrypted login credentials and cookies.

Revision ID: add_agent_credentials
"""

from alembic import op


revision = "add_agent_credentials"
down_revision = "add_tool_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create agent_credentials table (idempotent)."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            credential_type VARCHAR(20) DEFAULT 'website',
            platform VARCHAR(100) NOT NULL,
            display_name VARCHAR(200) DEFAULT '',
            username TEXT,
            password TEXT,
            login_url VARCHAR(500),
            cookies_json TEXT,
            cookies_updated_at TIMESTAMPTZ,
            status VARCHAR(20) DEFAULT 'active',
            last_login_at TIMESTAMPTZ,
            last_injected_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_credentials_agent_id "
        "ON agent_credentials(agent_id)"
    )


def downgrade() -> None:
    """Drop agent_credentials table."""
    op.execute("DROP INDEX IF EXISTS ix_agent_credentials_agent_id")
    op.execute("DROP TABLE IF EXISTS agent_credentials")
