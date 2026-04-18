"""User system refactor - unified migration.

Revision ID: user_refactor_v1
Revises: a1b2c3d4e5f6
Create Date: 2026-03-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'user_refactor_v1'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================
    # 1. Create identity_providers table (no foreign key to allow soft coupling)
    # ============================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS identity_providers (
            id UUID PRIMARY KEY,
            provider_type VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            config JSON,
            tenant_id UUID,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)

    # ============================================
    # 2. Create sso_scan_sessions table (no foreign keys for soft coupling)
    # ============================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS sso_scan_sessions (
            id UUID PRIMARY KEY,
            status VARCHAR(50) DEFAULT 'pending',
            provider_type VARCHAR(50),
            error_msg TEXT,
            tenant_id UUID,
            user_id UUID,
            access_token TEXT,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)

    # ============================================
    # 3. Alter tenants - add SSO fields
    # ============================================
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_enabled BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_domain VARCHAR(255)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_sso_domain ON tenants(sso_domain) WHERE sso_domain IS NOT NULL")

    # ============================================
    # 4. Alter org_departments (no foreign key - soft coupling via program)
    # ============================================
    op.execute("ALTER TABLE org_departments ADD COLUMN IF NOT EXISTS external_id VARCHAR(100)")
    op.execute("ALTER TABLE org_departments ADD COLUMN IF NOT EXISTS provider_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_departments_external_id ON org_departments(external_id)")
    # Note: provider_id is UUID without FK constraint - program should validate existence

    # ============================================
    # 5. Alter org_members (no foreign keys - soft coupling via program)
    # ============================================
    op.execute("""
        DO $$
        BEGIN
            -- 5.1 Handle feishu_open_id to open_id
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'feishu_open_id') THEN
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'open_id') THEN
                    UPDATE org_members SET open_id = feishu_open_id WHERE open_id IS NULL;
                    ALTER TABLE org_members DROP COLUMN feishu_open_id;
                ELSE
                    ALTER TABLE org_members RENAME COLUMN feishu_open_id TO open_id;
                END IF;
                ALTER TABLE org_members DROP CONSTRAINT IF EXISTS org_members_feishu_open_id_key;
            END IF;
            
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'open_id') THEN
                ALTER TABLE org_members ADD COLUMN open_id VARCHAR(100);
            END IF;

            -- 5.2 Handle feishu_user_id to external_id
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'feishu_user_id') THEN
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'external_id') THEN
                    UPDATE org_members SET external_id = feishu_user_id WHERE external_id IS NULL;
                    ALTER TABLE org_members DROP COLUMN feishu_user_id;
                ELSE
                    ALTER TABLE org_members RENAME COLUMN feishu_user_id TO external_id;
                END IF;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'org_members' AND column_name = 'external_id') THEN
                ALTER TABLE org_members ADD COLUMN external_id VARCHAR(100);
            END IF;
        END $$;
    """)

    op.execute("ALTER TABLE org_members ADD COLUMN IF NOT EXISTS unionid VARCHAR(100)")
    op.execute("ALTER TABLE org_members ADD COLUMN IF NOT EXISTS provider_id UUID")
    op.execute("ALTER TABLE org_members ADD COLUMN IF NOT EXISTS user_id UUID")
    
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_open_id ON org_members(open_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_external_id ON org_members(external_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_unionid ON org_members(unionid)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_user_id ON org_members(user_id)")


    # Note: provider_id and user_id are UUIDs without FK constraints - program should validate

    # ============================================
    # 5.1 Data migration - backfill org_members.provider_id (feishu)
    # ============================================
    op.execute("""
        UPDATE org_members AS om
        SET provider_id = ip.id
        FROM (
            SELECT DISTINCT ON (tenant_id) id, tenant_id
            FROM identity_providers
            WHERE provider_type = 'feishu'
            ORDER BY tenant_id, created_at DESC NULLS LAST, id
        ) AS ip
        WHERE om.tenant_id = ip.tenant_id
          AND om.provider_id IS NULL
    """)

    # ============================================
    # 6. Alter users - add new fields and constraints
    # ============================================
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_mobile VARCHAR(50)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS registration_source VARCHAR(50) DEFAULT 'web'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_primary_mobile ON users(primary_mobile)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_external_id ON users(external_id)")

    # Add unique constraints (partial indexes - allow multiple NULL values)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_users_tenant_email_unique'
            ) AND EXISTS (
                SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'email'
            ) THEN
                CREATE UNIQUE INDEX ix_users_tenant_email_unique ON users(tenant_id, email) WHERE email IS NOT NULL;
            END IF;
        END $$
    """)

    # Remove deprecated user identity columns (open_id / union_id)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'feishu_open_id'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT IF EXISTS users_feishu_open_id_key;
                DROP INDEX IF EXISTS ix_users_feishu_open_id;
                ALTER TABLE users DROP COLUMN feishu_open_id;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'feishu_union_id'
            ) THEN
                ALTER TABLE users DROP COLUMN feishu_union_id;
            END IF;
        END $$
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_users_tenant_mobile_unique'
            ) THEN
                CREATE UNIQUE INDEX ix_users_tenant_mobile_unique ON users(tenant_id, primary_mobile) WHERE primary_mobile IS NOT NULL;
            END IF;
        END $$
    """)

    # ============================================
    # 7. Drop deprecated departments table
    # ============================================
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS department_id")
    op.execute("DROP TABLE IF EXISTS departments")

    # ============================================
    # 8. Alter channel_config - extend app_secret length
    # ============================================
    # Note: This requires dropping and recreating the column due to PostgreSQL limitation
    # Only do this if the column exists and is smaller than 512
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'channel_config' AND column_name = 'app_secret'
                AND character_maximum_length < 512
            ) THEN
                ALTER TABLE channel_config ALTER COLUMN app_secret TYPE VARCHAR(512);
            END IF;
        END $$
    """)

    # Step 1: Get distinct tenant_ids from org_departments that haven't been migrated
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT DISTINCT od.tenant_id
        FROM org_departments od
        WHERE od.tenant_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM identity_providers ip
            WHERE ip.tenant_id = od.tenant_id
        )
    """))

    tenant_ids = [row[0] for row in result.fetchall()]

    for tenant_id in tenant_ids:
        # Generate provider ID using PostgreSQL function
        provider_id = connection.execute(sa.text("SELECT gen_random_uuid()")).scalar()

        # Insert IdentityProvider (only if not exists)
        connection.execute(
            sa.text("""
                INSERT INTO identity_providers (id, provider_type, name, is_active, config, tenant_id, created_at, updated_at)
                VALUES (:provider_id, 'feishu', 'Feishu SSO', TRUE, :config, :tenant_id, NOW(), NOW())
            """),
            {
                "provider_id": provider_id,
                "config": '{"app_id": "", "app_secret": ""}',
                "tenant_id": tenant_id
            }
        )

    # Step 2: Update org_departments - map feishu_id to external_id and link to provider
    # Only update rows where external_id is NULL (hasn't been migrated)
    connection.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'org_departments' AND column_name = 'feishu_id'
            ) THEN
                UPDATE org_departments od
                SET
                    external_id = od.feishu_id,
                    provider_id = ip.id
                FROM identity_providers ip
                WHERE od.tenant_id = ip.tenant_id
                  AND ip.provider_type = 'feishu'
                  AND od.tenant_id IS NOT NULL
                  AND od.external_id IS NULL;
            END IF;
        END $$
    """))

    # Step 3: Drop feishu_id column after migration
    op.execute("ALTER TABLE org_departments DROP COLUMN IF EXISTS feishu_id")
    # Drop legacy foreign key constraints that were added unexpectedly
    # Using IF EXISTS for safety across different environments
    op.execute("ALTER TABLE org_departments DROP CONSTRAINT IF EXISTS fk_org_departments_provider")
    op.execute("ALTER TABLE org_members DROP CONSTRAINT IF EXISTS fk_org_members_provider")
    # Add status column to org_departments for soft deletion during sync
    op.execute("ALTER TABLE org_departments ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'")

def downgrade() -> None:
    # ============================================
    # 8. Revert channel_config
    # ============================================
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'channel_config' AND column_name = 'app_secret'
            ) THEN
                ALTER TABLE channel_config ALTER COLUMN app_secret TYPE VARCHAR(255);
            END IF;
        END $$
    """)

    # ============================================
    # 7. Recreate departments table
    # ============================================
    op.create_table('departments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.VARCHAR(length=200), nullable=False),
        sa.Column('parent_id', sa.UUID(), nullable=True),
        sa.Column('manager_id', sa.UUID(), nullable=True),
        sa.Column('sort_order', sa.INTEGER(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['departments.id'], name='departments_parent_id_fkey'),
        sa.ForeignKeyConstraint(['manager_id'], ['users.id'], name='departments_manager_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='departments_pkey')
    )

    # ============================================
    # 6. Revert users constraints and columns
    # ============================================
    op.execute("DROP INDEX IF EXISTS ix_users_primary_mobile")
    op.execute("DROP INDEX IF EXISTS ix_users_tenant_email_unique")
    op.execute("DROP INDEX IF EXISTS ix_users_tenant_mobile_unique")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS primary_mobile")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS registration_source")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS feishu_open_id VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS feishu_union_id VARCHAR(255)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_open_id ON users(open_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_union_id ON users(union_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_feishu_open_id ON users(feishu_open_id)")

    # ============================================
    # 5. Revert org_members
    # ============================================
    op.execute("DROP INDEX IF EXISTS ix_org_members_user_id")
    op.execute("DROP INDEX IF EXISTS ix_org_members_unionid")
    op.execute("DROP INDEX IF EXISTS ix_org_members_external_id")
    op.execute("ALTER TABLE org_members DROP CONSTRAINT IF EXISTS fk_org_members_user")
    op.execute("ALTER TABLE org_members DROP CONSTRAINT IF EXISTS fk_org_members_provider")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS provider_id")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS unionid")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS external_id")

    # ============================================
    # 4. Revert org_departments
    # ============================================
    op.execute("DROP INDEX IF EXISTS ix_org_departments_external_id")
    op.execute("ALTER TABLE org_departments DROP CONSTRAINT IF EXISTS fk_org_departments_provider")
    op.execute("ALTER TABLE org_departments DROP COLUMN IF EXISTS provider_id")
    op.execute("ALTER TABLE org_departments DROP COLUMN IF EXISTS external_id")

    # ============================================
    # 3. Revert tenants
    # ============================================
    op.execute("DROP INDEX IF EXISTS ux_tenants_sso_domain")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS sso_domain")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS sso_enabled")

    # ============================================
    # 2. Drop sso_scan_sessions
    # ============================================
    op.drop_table('sso_scan_sessions')

    # ============================================
    # 1. Drop identity_providers
    # ============================================
    op.drop_table('identity_providers')

    # Note: Downgrade is NOT idempotent - it resets data
    # In production, you may want to skip this or make it optional
    connection = op.get_bind()

    # Add back feishu_id column
    op.execute("ALTER TABLE org_departments ADD COLUMN IF NOT EXISTS feishu_id VARCHAR(100)")

    # Restore feishu_id from external_id
    connection.execute(sa.text("""
        UPDATE org_departments
        SET feishu_id = external_id
        WHERE external_id IS NOT NULL
    """))

    # Delete the identity providers created by this migration
    connection.execute(sa.text("""
        DELETE FROM identity_providers
        WHERE provider_type = 'feishu'
        AND config::text = '{"app_id": "", "app_secret": ""}'
    """))