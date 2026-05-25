"""Trigger daemon orchestrator.

Trigger-specific evaluation and invocation behavior now lives under
`app.services.trigger_runtime`. This module owns the main loop, dedup window,
and distributed claim/invoke flow.
"""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.trigger import AgentTrigger
from app.services.trigger_runtime.evaluator import (
    evaluate_trigger as evaluate_trigger_runtime,
    handle_okr_collection_trigger as handle_okr_collection_trigger_runtime,
    handle_okr_report_trigger as handle_okr_report_trigger_runtime,
    mark_trigger_fired as mark_trigger_fired_runtime,
    mark_trigger_skipped as mark_trigger_skipped_runtime,
    should_skip_non_workday as should_skip_non_workday_runtime,
)
from app.services.trigger_runtime.invoker import invoke_agent_for_triggers as invoke_agent_for_triggers_runtime
from app.services.trigger_runtime import (
    claim_ready_trigger_invocations,
    enqueue_due_trigger,
    mark_trigger_executions_completed,
    mark_trigger_executions_failed,
)

TICK_INTERVAL = 15  # seconds
DEDUP_WINDOW = 30   # seconds — same agent won't be invoked twice within this window
MAX_AGENT_CHAIN_DEPTH = 5  # A→B→A→B→A max depth before stopping
MIN_POLL_INTERVAL_MINUTES = 5  # minimum poll interval to prevent abuse

_last_invoke: dict[uuid.UUID, datetime] = {}

_A2A_WAKE_CHAIN: dict[str, int] = {}
_A2A_WAKE_CHAIN_TTL = 300
_A2A_MAX_WAKE_DEPTH = 3


def _cleanup_stale_invoke_cache():
    now = datetime.now(timezone.utc)
    stale = [k for k, v in _last_invoke.items() if (now - v).total_seconds() > DEDUP_WINDOW * 2]
    for k in stale:
        del _last_invoke[k]


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

async def _invoke_agent_for_triggers(agent_id: uuid.UUID, triggers: list[AgentTrigger]):
    await invoke_agent_for_triggers_runtime(agent_id, triggers)


# ── Main Tick Loop ──────────────────────────────────────────────────

async def _tick():
    """One daemon tick: evaluate all triggers, group by agent, invoke."""
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(AgentTrigger.is_enabled == True)
        )
        all_triggers = result.scalars().all()

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
                    await enqueue_due_trigger(trigger, now)
        except Exception as e:
            logger.warning(f"Error evaluating trigger {trigger.name}: {e}")

    # Claim queued executions with a DB lease so only one worker handles each event.
    try:
        fired_by_agent, force_invoke_agents = await claim_ready_trigger_invocations(now)
    except Exception as e:
        logger.warning(f"Failed to claim trigger executions: {e}")
        fired_by_agent = {}
        force_invoke_agents = set()

    # Invoke each agent (with dedup window)
    for agent_id, agent_triggers in fired_by_agent.items():
        last = _last_invoke.get(agent_id)
        if agent_id not in force_invoke_agents and last and (now - last).total_seconds() < DEDUP_WINDOW:
            continue  # Skip — invoked too recently
        _last_invoke[agent_id] = now

        # ── Immediately update trigger state BEFORE launching async task ──
        # This prevents the next tick from re-evaluating the same trigger as
        # "should fire" while the LLM call is still running (which can take
        # minutes). Without this, the 15s tick interval + 30s dedup window
        # would cause repeated invocations for long-running triggers.
        try:
            async with async_session() as db:
                for t in agent_triggers:
                    cfg = t.config or {}
                    if isinstance(cfg, str):
                        import json
                        try:
                            cfg = json.loads(cfg)
                        except (json.JSONDecodeError, TypeError):
                            cfg = {}
                    if cfg.get("_execution_id"):
                        continue
                    result = await db.execute(
                        select(AgentTrigger).where(AgentTrigger.id == t.id)
                    )
                    trigger = result.scalar_one_or_none()
                    if trigger:
                        trigger.last_fired_at = now
                        trigger.fire_count += 1
                        # Auto-disable single-shot types only
                        if trigger.type == "once":
                            trigger.is_enabled = False
                        if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
                            trigger.is_enabled = False
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to pre-update trigger state: {e}")

        asyncio.create_task(_invoke_agent_for_triggers(agent_id, agent_triggers))


async def wake_agent_with_context(agent_id: uuid.UUID, message_context: str, *, from_agent_id: uuid.UUID | None = None, skip_dedup: bool = False, a2a_session_id: str | None = None) -> None:
    """Public API: wake an agent asynchronously with a message context.

    Creates a synthetic trigger invocation so the agent processes the
    message in a Reflection Session via the standard trigger path.
    If a2a_session_id is provided, the agent's reply will also be saved
    to the A2A chat session for visibility in the admin chat history.
    Safe to call from any async context.

    Args:
        agent_id: The agent to wake.
        message_context: The message to deliver.
        from_agent_id: The agent that initiated this wake (for chain depth tracking).
        skip_dedup: If True, bypass the dedup window check.
        a2a_session_id: Optional A2A chat session ID to mirror the reply into.
    """
    import time as _time

    now = datetime.now(timezone.utc)

    if from_agent_id:
        chain_key = f"{from_agent_id}->{agent_id}"
        current_depth = _A2A_WAKE_CHAIN.get(chain_key, 0)
        if current_depth >= _A2A_MAX_WAKE_DEPTH:
            logger.warning(
                f"[A2A] Wake chain depth {current_depth} reached for {chain_key}, "
                f"stopping to prevent wake storm"
            )
            return

        _A2A_WAKE_CHAIN[chain_key] = current_depth + 1

        def _decay_chain():
            _A2A_WAKE_CHAIN.pop(chain_key, None)
        asyncio.get_running_loop().call_later(_A2A_WAKE_CHAIN_TTL, _decay_chain)

    if not skip_dedup and agent_id in _last_invoke:
        elapsed = (now - _last_invoke[agent_id]).total_seconds()
        if elapsed < DEDUP_WINDOW:
            logger.info(
                f"[A2A] Skipping wake for agent {agent_id} — "
                f"invoked {elapsed:.0f}s ago (dedup window {DEDUP_WINDOW}s)"
            )
            return

    _last_invoke[agent_id] = now

    dummy_trigger = AgentTrigger(
        id=uuid.uuid4(),
        agent_id=agent_id,
        name="a2a_wake",
        type="on_message",
        config={"from_agent_name": "", "_matched_message": message_context[:2000], "_matched_from": "agent", "_a2a_session_id": a2a_session_id},
        reason=(
            "You received a notification from another agent. "
            "Read the message content above, update your focus and memory if needed, "
            "and take any action you deem necessary. "
            "Do NOT reply back to the sender unless you have a genuine question — "
            "this was a notification, not a request for response."
        ),
        is_enabled=True,
        last_fired_at=now,
        fire_count=0,
    )
    asyncio.create_task(_invoke_agent_for_triggers(agent_id, [dummy_trigger]))


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

        await asyncio.sleep(TICK_INTERVAL)
