"""Add Microsoft Teams support to im_provider and channel_type enums."""

from alembic import op
import sqlalchemy as sa

revision = "add_microsoft_teams_support"
down_revision = "add_agent_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'microsoft_teams' to im_provider_enum
    op.execute("ALTER TYPE im_provider_enum ADD VALUE IF NOT EXISTS 'microsoft_teams'")
    
    # Add 'teams' to channel_type_enum (note: using 'teams' not 'microsoft_teams' for consistency with other channels)
    op.execute("ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS 'microsoft_teams'")
    
    # Fix: Rename agenda_ref to focus_ref in agent_triggers table (if column exists)
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns 
                      WHERE table_name='agent_triggers' AND column_name='agenda_ref') THEN
                ALTER TABLE agent_triggers RENAME COLUMN agenda_ref TO focus_ref;
            END IF;
        END $$;
    """)
    
    # Add missing columns to agents table (idempotent)
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS welcome_message TEXT")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_triggers INTEGER DEFAULT 20")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS min_poll_interval_min INTEGER DEFAULT 5")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS webhook_rate_limit INTEGER DEFAULT 5")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS timezone VARCHAR(50)")
    
    # Add missing columns to tenants table (idempotent)
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_triggers INTEGER DEFAULT 20")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS min_poll_interval_floor INTEGER DEFAULT 5")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_webhook_rate_ceiling INTEGER DEFAULT 5")


def downgrade() -> None:
    # Fix: Rename focus_ref back to agenda_ref in agent_triggers table (if column exists)
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns 
                      WHERE table_name='agent_triggers' AND column_name='focus_ref') THEN
                ALTER TABLE agent_triggers RENAME COLUMN focus_ref TO agenda_ref;
            END IF;
        END $$;
    """)
    # Note: PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type, which is complex
    # For now, we'll leave the enum values in place
    pass
