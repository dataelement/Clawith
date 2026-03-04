"""Add invitation_codes table.

Run: cd backend && .venv/bin/python alembic/versions/add_invitation_codes.py
"""

import asyncio
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS invitation_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) NOT NULL UNIQUE,
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invitation_codes_code ON invitation_codes(code);
"""


async def run_migration():
    from app.database import engine

    async with engine.begin() as conn:
        for statement in MIGRATION_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                await conn.execute(text(stmt))
                logger.info(f"Executed: {stmt[:60]}...")

    logger.info("✅ invitation_codes migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migration())
