"""Add provider-aware org sync fields.

Revision ID: add_wecom_org_sync_fields
Revises: add_daily_token_usage
"""

from alembic import op

revision = "add_wecom_org_sync_fields"
down_revision = "add_daily_token_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE org_departments
        ADD COLUMN IF NOT EXISTS wecom_id VARCHAR(100)
        """
    )
    op.execute(
        """
        ALTER TABLE org_departments
        ADD COLUMN IF NOT EXISTS sync_provider VARCHAR(20) NOT NULL DEFAULT 'feishu'
        """
    )
    op.execute(
        """
        ALTER TABLE org_members
        ADD COLUMN IF NOT EXISTS wecom_user_id VARCHAR(100)
        """
    )
    op.execute(
        """
        ALTER TABLE org_members
        ADD COLUMN IF NOT EXISTS sync_provider VARCHAR(20) NOT NULL DEFAULT 'feishu'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_departments_wecom_id ON org_departments(wecom_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_departments_sync_provider ON org_departments(sync_provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_members_wecom_user_id ON org_members(wecom_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_members_sync_provider ON org_members(sync_provider)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_org_members_sync_provider")
    op.execute("DROP INDEX IF EXISTS ix_org_members_wecom_user_id")
    op.execute("DROP INDEX IF EXISTS ix_org_departments_sync_provider")
    op.execute("DROP INDEX IF EXISTS ix_org_departments_wecom_id")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS sync_provider")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS wecom_user_id")
    op.execute("ALTER TABLE org_departments DROP COLUMN IF EXISTS sync_provider")
    op.execute("ALTER TABLE org_departments DROP COLUMN IF EXISTS wecom_id")
