"""Add gws_oauth_tokens, tenant_settings, gateway_messages tables

Revision ID: add_gws_and_settings_tables
Revises: d9cbd43b62e5
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "add_gws_and_settings_tables"
down_revision: Union[str, None] = "d9cbd43b62e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS gws_oauth_tokens (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tenant_id UUID REFERENCES tenants(id),
            google_email TEXT NOT NULL,
            google_user_id VARCHAR(255),
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            token_expiry TIMESTAMPTZ,
            scopes TEXT[],
            status VARCHAR(20) DEFAULT 'active',
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_gws_oauth_agent_user UNIQUE (agent_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gws_oauth_agent_id ON gws_oauth_tokens(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gws_oauth_user_id ON gws_oauth_tokens(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gws_oauth_tenant_id ON gws_oauth_tokens(tenant_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_settings (
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            key VARCHAR(100) NOT NULL,
            value JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (tenant_id, key)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS gateway_messages (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL REFERENCES agents(id),
            sender_agent_id UUID REFERENCES agents(id),
            sender_user_id UUID REFERENCES users(id),
            conversation_id VARCHAR(100),
            content TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'pending' NOT NULL,
            result TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            delivered_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gateway_messages")
    op.execute("DROP TABLE IF EXISTS tenant_settings")
    op.execute("DROP TABLE IF EXISTS gws_oauth_tokens")
