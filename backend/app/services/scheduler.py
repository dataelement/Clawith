"""Lightweight asyncio scheduler for durable Agent Runtime cron jobs.

Runs as a background task inside the FastAPI process.
Every 30 seconds, checks for schedules whose next_run_at <= now
and registers each occurrence on the shared Runtime.
"""

import asyncio
from datetime import datetime, timezone

from croniter import croniter
from loguru import logger
from sqlalchemy import select


def compute_next_run(cron_expr: str, after: datetime | None = None) -> datetime | None:
    """Compute the next run time from a cron expression."""
    try:
        base = after or datetime.now(timezone.utc)
        cron = croniter(cron_expr, base)
        return cron.get_next(datetime).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.error(f"Invalid cron expression '{cron_expr}': {e}")
        return None


async def _tick():
    """One scheduler tick: find and execute due schedules."""
    from app.database import async_session
    from app.core.permissions import is_agent_expired
    from app.models.agent import Agent
    from app.models.schedule import AgentSchedule
    from app.services.audit_logger import write_audit_log
    from app.services.heartbeat_runtime import (
        enqueue_schedule_runtime,
        schedule_occurrence_id,
    )

    now = datetime.now(timezone.utc)

    try:
        async with async_session() as db:
            result = await db.execute(
                select(AgentSchedule).where(
                    AgentSchedule.is_enabled.is_(True),
                    AgentSchedule.next_run_at <= now,
                ).with_for_update(skip_locked=True)
            )
            due_schedules = result.scalars().all()

            if due_schedules:
                await write_audit_log("schedule_tick", {"due_count": len(due_schedules)})

            for sched in due_schedules:
                occurrence_at = sched.next_run_at
                if occurrence_at is None:
                    continue
                agent_result = await db.execute(
                    select(Agent).where(Agent.id == sched.agent_id)
                )
                agent = agent_result.scalar_one_or_none()
                if (
                    agent is None
                    or agent.status != "running"
                    or is_agent_expired(agent)
                ):
                    logger.info(
                        f"Schedule {sched.id}: Agent unavailable; advancing occurrence without execution"
                    )
                    sched.last_run_at = now
                    sched.next_run_at = compute_next_run(sched.cron_expr, now)
                    sched.run_count = (sched.run_count or 0) + 1
                    await db.commit()
                    continue

                handle = await enqueue_schedule_runtime(
                    db,
                    agent=agent,
                    schedule_id=sched.id,
                    occurrence_id=schedule_occurrence_id(sched.id, occurrence_at),
                    instruction=sched.instruction,
                )
                if handle is None:
                    logger.error(
                        f"Schedule {sched.id}: Runtime disabled; occurrence remains due"
                    )
                    await db.rollback()
                    return

                next_run = compute_next_run(sched.cron_expr, now)
                sched.last_run_at = now
                sched.next_run_at = next_run
                sched.run_count = (sched.run_count or 0) + 1
                await db.commit()

                await write_audit_log(
                    "schedule_fire",
                    {
                        "schedule_id": str(sched.id),
                        "name": sched.name,
                        "instruction": sched.instruction[:100],
                        "next_run": str(next_run),
                    },
                    agent_id=sched.agent_id,
                )

                logger.info(
                    f"Queued schedule '{sched.name}' as Run {handle.run_id} (next: {next_run})"
                )

    except Exception as e:
        logger.exception(f"Scheduler tick error: {e}")
        await write_audit_log("schedule_error", {"error": str(e)[:300]})


async def start_scheduler():
    """Start the background scheduler loop. Call from FastAPI startup."""
    logger.info("🕐 Agent scheduler started (30s interval)")
    while True:
        await _tick()
        await asyncio.sleep(30)
