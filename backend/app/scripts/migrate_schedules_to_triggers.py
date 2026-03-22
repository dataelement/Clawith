"""Migrate existing AgentSchedule records to AgentTrigger (cron type).

Run this script once after deploying Phase 2 of the Aware engine.
It converts all existing agent_schedules into agent_triggers with type='cron'.

Usage:
    python -m app.scripts.migrate_schedules_to_triggers
"""
import asyncio
import uuid
from datetime import datetime, timezone

import logging
logger = logging.getLogger(__name__)
from sqlalchemy import select

from app.database import async_session
from app.models.agent import Agent  # noqa: F401 — needed for FK resolution
from app.models.schedule import AgentSchedule
from app.models.trigger import AgentTrigger


async def migrate():
    """Convert all AgentSchedule records to AgentTrigger(type='cron')."""
    async with async_session() as db:
        result = await db.execute(select(AgentSchedule))
        schedules = result.scalars().all()

        if not schedules:
            logger.info("No schedules found to migrate.")
            return

        migrated = 0
        skipped = 0
        for s in schedules:
            # Check if trigger already exists for this schedule
            existing = await db.execute(
                select(AgentTrigger).where(
                    AgentTrigger.agent_id == s.agent_id,
                    AgentTrigger.name == f"migrated_{s.name[:80]}",
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"  Skip: '{s.name}' already migrated")
                skipped += 1
                continue

            trigger = AgentTrigger(
                agent_id=s.agent_id,
                name=f"migrated_{s.name[:80]}",
                type="cron",
                config={"expr": s.cron_expr},
                reason=s.instruction[:500] if s.instruction else f"Migrated schedule: {s.name}",
                is_enabled=s.is_enabled,
                fire_count=s.run_count or 0,
                last_fired_at=s.last_run_at,
            )
            db.add(trigger)
            # Disable the source schedule so it won't be re-migrated
            # if the user deletes the trigger and this script runs again
            s.is_enabled = False
            migrated += 1
            logger.info(f"  Migrated: '{s.name}' -> cron({s.cron_expr})")

        await db.commit()
        logger.info(f"Migration complete: {migrated} migrated, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(migrate())
