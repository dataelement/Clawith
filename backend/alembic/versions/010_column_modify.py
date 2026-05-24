"""Unified column fix for missing fields across main tables.

Revision ID: 20260313_column_modify
Revises: add_microsoft_teams_support
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260313_column_modify"
down_revision = "add_microsoft_teams_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── AGENTS Table ───────────────────────────────────────
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS welcome_message TEXT")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS bio TEXT")
    
    # Token usage & Reset
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_total INTEGER DEFAULT 0")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_daily_reset TIMESTAMPTZ")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_monthly_reset TIMESTAMPTZ")
    
    # Heartbeat (Pulse Engine)
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS heartbeat_enabled BOOLEAN DEFAULT TRUE")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS heartbeat_interval_minutes INTEGER DEFAULT 120")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS heartbeat_active_hours VARCHAR(20) DEFAULT '09:00-18:00'")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ")
    
    # Trigger Limits & Context
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_triggers INTEGER DEFAULT 20")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS min_poll_interval_min INTEGER DEFAULT 5")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS webhook_rate_limit INTEGER DEFAULT 5")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS timezone VARCHAR(50)")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ")

    # ─── USERS Table ────────────────────────────────────────
    # Feishu & Identity
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS feishu_union_id VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS feishu_user_id VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS title VARCHAR(100)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    # Department (Skip FK for now to avoid dependency issues if table not created by migrations)
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS department_id UUID")

    # ─── TENANTS Table ──────────────────────────────────────
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS im_config JSONB")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    # Add missing fields to tenants table
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_triggers INTEGER DEFAULT 20")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS min_poll_interval_floor INTEGER DEFAULT 5")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_webhook_rate_ceiling INTEGER DEFAULT 5")


    # ─── AGENT_TRIGGERS (Rename Fix) ────────────────────────
    # If the previous migration created 'agenda_ref', rename it to 'focus_ref'
    # Check if table has agenda_ref and doesn't have focus_ref
    op.execute("""
        DO $$ 
        BEGIN 
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agent_triggers' AND column_name='agenda_ref') 
            AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agent_triggers' AND column_name='focus_ref') THEN
                ALTER TABLE agent_triggers RENAME COLUMN agenda_ref TO focus_ref;
            END IF;
        END $$;
    """)
    # Ensure focus_ref exists (if fresh)
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS focus_ref VARCHAR(200)")


def downgrade() -> None:
    # Downgrade is optional for column added idempotently, but good for completeness
    # PostgreSQL doesn't support DROP COLUMN IF EXISTS in all versions, but 9.6+ does.
    pass
