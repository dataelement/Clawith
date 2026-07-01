"""add_agent_directory_indexes

Revision ID: add_agent_directory_indexes
Revises: add_title_to_agent_focus_items
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "add_agent_directory_indexes"
down_revision: Union[str, None] = "add_title_to_agent_focus_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_tenant_access_status_name "
        "ON agents (tenant_id, access_mode, status, name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_tenant_creator_access "
        "ON agents (tenant_id, creator_id, access_mode)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_permissions_agent_scope_scopeid_level "
        "ON agent_permissions (agent_id, scope_type, scope_id, access_level)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_permissions_scopeid_scope_agent "
        "ON agent_permissions (scope_id, scope_type, agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_agent_relationships_agent_target "
        "ON agent_agent_relationships (agent_id, target_agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_members_tenant_status_name "
        "ON org_members (tenant_id, status, name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_members_tenant_user "
        "ON org_members (tenant_id, user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_org_members_tenant_user")
    op.execute("DROP INDEX IF EXISTS ix_org_members_tenant_status_name")
    op.execute("DROP INDEX IF EXISTS ix_agent_agent_relationships_agent_target")
    op.execute("DROP INDEX IF EXISTS ix_agent_permissions_scopeid_scope_agent")
    op.execute("DROP INDEX IF EXISTS ix_agent_permissions_agent_scope_scopeid_level")
    op.execute("DROP INDEX IF EXISTS ix_agents_tenant_creator_access")
    op.execute("DROP INDEX IF EXISTS ix_agents_tenant_access_status_name")
