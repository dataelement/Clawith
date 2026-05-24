"""Multi-tenant registration: add tenant_id to invitation_codes, delete
historical codes, assign org_admin to earliest company members.

Revision ID: multi_tenant_registration
Revises: add_llm_max_output_tokens
"""
from alembic import op

revision = "multi_tenant_registration"
down_revision = "add_skill_tenant_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add tenant_id to invitation_codes
    op.execute(
        "ALTER TABLE invitation_codes "
        "ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invitation_codes_tenant_id "
        "ON invitation_codes (tenant_id)"
    )

    # 2. Delete all historical invitation codes (they are platform-level, no longer used)
    op.execute("DELETE FROM invitation_codes")

    # 3. For each tenant, set the earliest joined user as org_admin
    #    (skip platform_admin users — they already have higher privileges)
    op.execute("""
        UPDATE users
        SET role = 'org_admin'
        WHERE id IN (
            SELECT DISTINCT ON (tenant_id) id
            FROM users
            WHERE tenant_id IS NOT NULL
              AND role NOT IN ('platform_admin', 'org_admin')
            ORDER BY tenant_id, created_at ASC
        )
    """)

    # 4. Add allow_self_create_company platform setting (default: true)
    op.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ('allow_self_create_company', '{"enabled": true}'::jsonb)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_invitation_codes_tenant_id")
    op.execute(
        "ALTER TABLE invitation_codes DROP COLUMN IF EXISTS tenant_id"
    )
    op.execute("DELETE FROM system_settings WHERE key = 'allow_self_create_company'")
