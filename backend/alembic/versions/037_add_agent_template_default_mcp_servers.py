"""Add default_mcp_servers to agent templates.

Revision ID: add_default_mcp_servers
Revises: add_agent_user_onboardings
Create Date: 2026-04-27

Lets a template declare a list of Smithery server IDs (e.g. "shibui/finance")
that should auto-install at agent-creation time using the system-level
Smithery API key. The new-agent handler in api.agents.create_agent calls
import_mcp_from_smithery for each entry, then binds the resulting Tool(s)
to the new agent via AgentTool. Idempotent — if a Tool with the same
mcp_server_url already exists, it's reused without a re-import.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'add_default_mcp_servers'
down_revision: Union[str, None] = 'add_agent_user_onboardings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_templates "
        "ADD COLUMN IF NOT EXISTS default_mcp_servers JSON DEFAULT '[]'::json"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agent_templates DROP COLUMN IF EXISTS default_mcp_servers"
    )
