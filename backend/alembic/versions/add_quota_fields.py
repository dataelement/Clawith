"""Add usage quota fields to users, agents, and tenants tables.

Run with: cd backend && python -m alembic.versions.add_quota_fields
Or manually: psql -d clawith -f <this_file_as_sql>

For fresh installs, seed.py handles table creation automatically.
"""

import asyncio
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
-- ═══ Users table: quota fields ═══
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_limit INTEGER DEFAULT 50;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_period VARCHAR(20) DEFAULT 'permanent';
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_messages_used INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_max_agents INTEGER DEFAULT 2;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_agent_ttl_hours INTEGER DEFAULT 48;

-- ═══ Agents table: expiry + LLM call tracking ═══
ALTER TABLE agents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_today INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_llm_calls_per_day INTEGER DEFAULT 100;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_reset_at TIMESTAMPTZ;

-- ═══ Tenants table: default quotas + heartbeat floor ═══
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_message_limit INTEGER DEFAULT 50;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_message_period VARCHAR(20) DEFAULT 'permanent';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_agents INTEGER DEFAULT 2;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_agent_ttl_hours INTEGER DEFAULT 48;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS default_max_llm_calls_per_day INTEGER DEFAULT 100;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS min_heartbeat_interval_minutes INTEGER DEFAULT 120;
"""


async def run_migration():
    """Execute migration against the configured database."""
    from app.database import engine

    async with engine.begin() as conn:
        for statement in MIGRATION_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                await conn.execute(text(stmt))
                logger.info(f"Executed: {stmt[:60]}...")

    logger.info("✅ Quota migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migration())
