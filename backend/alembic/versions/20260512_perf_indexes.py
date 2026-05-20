"""Add performance indexes for registration and OKR hooks.

Revision ID: perf_indexes_20260512
Revises: add_agent_focus_items
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'perf_indexes_20260512'
down_revision = 'add_agent_focus_items'
branch_labels = None
depends_on = None

def upgrade():
    # 1. Indexes for org_members to speed up RegistrationService.bind_org_member
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_email ON org_members(email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_phone ON org_members(phone)")
    # Note: user_id index might already exist from user_refactor_v1, but we use IF NOT EXISTS for safety
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_user_id ON org_members(user_id)")

    # 2. Indexes for agents to speed up _get_okr_agent lookups
    op.execute("CREATE INDEX IF NOT EXISTS ix_agents_tenant_system_name ON agents(tenant_id, is_system, name)")

def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_org_members_email")
    op.execute("DROP INDEX IF EXISTS ix_org_members_phone")
    op.execute("DROP INDEX IF EXISTS ix_agents_tenant_system_name")
