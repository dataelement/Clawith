"""Trigger daemon orchestrator.

Trigger-specific evaluation and invocation behavior now lives under
`app.services.trigger_runtime`. This module owns the main loop, dedup window,
and distributed claim/invoke flow.
"""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import delete, select

from app.core.logging_config import new_trace_id
from app.database import async_session
from app.models.experience import ExperienceEntry
from app.models.trigger import AgentTrigger
from app.services.trigger_runtime.evaluator import (
    evaluate_trigger as evaluate_trigger_runtime,
    handle_okr_collection_trigger as handle_okr_collection_trigger_runtime,
    handle_okr_report_trigger as handle_okr_report_trigger_runtime,
    mark_trigger_fired as mark_trigger_fired_runtime,
    mark_trigger_skipped as mark_trigger_skipped_runtime,
    should_skip_non_workday as should_skip_non_workday_runtime,
)
from app.services.trigger_runtime import enqueue_due_trigger

TICK_INTERVAL = 15  # seconds
MIN_POLL_INTERVAL_MINUTES = 5  # minimum poll interval to prevent abuse

# Safety: per-agent on_message fire rate limiter
_ON_MSG_RATE_WINDOW = 3600  # 1 hour window
_ON_MSG_RATE_LIMIT = 30     # max on_message fires per agent per hour
_on_msg_fire_log: dict[uuid.UUID, list[datetime]] = {}  # agent_id -> list of fire timestamps

def _cleanup_stale_invoke_cache():
    now = datetime.now(timezone.utc)
    # Clean up old on_message rate limiter entries
    cutoff = now - timedelta(seconds=_ON_MSG_RATE_WINDOW)
    stale_agents = []
    for aid, timestamps in _on_msg_fire_log.items():
        _on_msg_fire_log[aid] = [t for t in timestamps if t > cutoff]
        if not _on_msg_fire_log[aid]:
            stale_agents.append(aid)
    for aid in stale_agents:
        del _on_msg_fire_log[aid]


_RETIRED_EXPERIENCE_TTL_DAYS = 30
_last_exp_purge_day = None  # date of the last purge; runs at most once per UTC day


async def _purge_expired_retired_experiences():
    """Hard-delete experience entries retired more than 30 days ago and not re-published.

    Re-publishing clears `retired_at`, so only entries still sitting in the 已下架 bin
    past the TTL are removed. experience_references cascade at the DB level. Runs once
    per day off the daemon tick.
    """
    global _last_exp_purge_day
    today = datetime.now(timezone.utc).date()
    if _last_exp_purge_day == today:
        return
    _last_exp_purge_day = today
    cutoff = datetime.now(timezone.utc) - timedelta(days=_RETIRED_EXPERIENCE_TTL_DAYS)
    async with async_session() as db:
        ids = (
            await db.execute(
                select(ExperienceEntry.id).where(
                    ExperienceEntry.status == "retired",
                    ExperienceEntry.retired_at.is_not(None),
                    ExperienceEntry.retired_at < cutoff,
                )
            )
        ).scalars().all()
        if not ids:
            return
        await db.execute(delete(ExperienceEntry).where(ExperienceEntry.id.in_(ids)))
        await db.commit()
        logger.info(f"🧹 Purged {len(ids)} retired experience entries older than {_RETIRED_EXPERIENCE_TTL_DAYS}d")


async def _should_skip_non_workday(trigger: AgentTrigger, local_now: datetime) -> bool:
    return await should_skip_non_workday_runtime(trigger, local_now)


async def _mark_trigger_skipped(trigger_id: uuid.UUID, now: datetime) -> None:
    await mark_trigger_skipped_runtime(trigger_id, now)


async def _mark_trigger_fired(trigger_id: uuid.UUID, now: datetime) -> None:
    await mark_trigger_fired_runtime(trigger_id, now)


async def _handle_okr_report_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    return await handle_okr_report_trigger_runtime(trigger, now)


async def _handle_okr_collection_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    return await handle_okr_collection_trigger_runtime(trigger, now)

async def _evaluate_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    return await evaluate_trigger_runtime(trigger, now)

# ── Main Tick Loop ──────────────────────────────────────────────────

async def _tick():
    """One daemon tick: evaluate all triggers, group by agent, invoke."""
    new_trace_id()
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(AgentTrigger.is_enabled.is_(True))
        )
        all_triggers = result.scalars().all()
        # Expunge each object before session.close() is called.
        # session.close() expires all objects still in the identity map;
        # explicit expunge() detaches them WITHOUT expiry so their scalar
        # attributes remain readable outside the session context.
        for _t in all_triggers:
            db.expunge(_t)

    if not all_triggers:
        return


    # Evaluate and enqueue due triggers. Agent invocation happens only after
    # executions are claimed through the distributed execution queue.
    for trigger in all_triggers:
        # Auto-disable expired triggers
        if trigger.expires_at and now >= trigger.expires_at:
            async with async_session() as db:
                result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger.id))
                t = result.scalar_one_or_none()
                if t:
                    t.is_enabled = False
                    await db.commit()
            continue

        try:
            if await _evaluate_trigger(trigger, now):
                handled = await _handle_okr_report_trigger(trigger, now)
                if not handled:
                    handled = await _handle_okr_collection_trigger(trigger, now)
                if not handled:
                    # Fix 3: Rate limit on_message triggers per agent
                    if trigger.type == "on_message":
                        agent_fires = _on_msg_fire_log.get(trigger.agent_id, [])
                        cutoff = now - timedelta(seconds=_ON_MSG_RATE_WINDOW)
                        recent = [t for t in agent_fires if t > cutoff]
                        if len(recent) >= _ON_MSG_RATE_LIMIT:
                            logger.warning(
                                f"[A2A Safety] Agent {trigger.agent_id} hit "
                                f"on_message rate limit ({_ON_MSG_RATE_LIMIT}/hr). "
                                f"Auto-disabling trigger '{trigger.name}'."
                            )
                            async with async_session() as db:
                                result = await db.execute(
                                    select(AgentTrigger).where(AgentTrigger.id == trigger.id)
                                )
                                t_obj = result.scalar_one_or_none()
                                if t_obj:
                                    t_obj.is_enabled = False
                                    await db.commit()
                            continue
                        recent.append(now)
                        _on_msg_fire_log[trigger.agent_id] = recent
                    await enqueue_due_trigger(trigger, now)
        except Exception as e:
            logger.warning(f"Error evaluating trigger {trigger.name}: {e}")

async def start_trigger_daemon():
    """Start the background trigger daemon loop. Called from FastAPI startup."""
    logger.info("⚡ Trigger Daemon started (15s tick, heartbeat every ~60s)")
    _heartbeat_counter = 0
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"Trigger Daemon error: {e}")
            import traceback
            traceback.print_exc()

        # Run heartbeat check every 4th tick (~60 seconds)
        _heartbeat_counter += 1
        if _heartbeat_counter >= 4:
            _heartbeat_counter = 0
            _cleanup_stale_invoke_cache()
            try:
                from app.services.heartbeat import _heartbeat_tick
                await _heartbeat_tick()
            except Exception as e:
                logger.error(f"Heartbeat tick error: {e}")
            try:
                await _purge_expired_retired_experiences()
            except Exception as e:
                logger.error(f"Retired-experience purge error: {e}")

        await asyncio.sleep(TICK_INTERVAL)
