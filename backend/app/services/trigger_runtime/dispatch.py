"""Dispatch helpers for trigger executions."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.database import async_session
from app.models.trigger import AgentTrigger
from app.services.trigger_runtime.executions import (
    build_execution_runtime_trigger,
    claim_pending_trigger_executions,
    mark_base_triggers_fired,
)
from app.services.trigger_runtime.keys import build_scheduled_execution_key
from app.services.trigger_runtime.queue import enqueue_trigger_execution


def runtime_execution_payload(trigger: AgentTrigger) -> dict:
    """Capture ephemeral trigger evaluation context into an execution payload."""
    cfg = trigger.config or {}
    payload: dict = {}
    for key in (
        "_matched_message",
        "_matched_from",
        "okr_member_id",
        "okr_member_type",
        "okr_report_date",
        "_notification_summary",
        "_origin_session_id",
        "_origin_user_id",
        "_origin_source_channel",
        "_a2a_session_id",
    ):
        if key in cfg and cfg.get(key) is not None:
            payload[key] = cfg.get(key)
    return payload


async def enqueue_due_trigger(trigger: AgentTrigger, now: datetime) -> None:
    async with async_session() as db:
        await enqueue_trigger_execution(
            db,
            trigger=trigger,
            source=trigger.type,
            idempotency_key=build_scheduled_execution_key(trigger, now),
            payload_obj=runtime_execution_payload(trigger),
        )


async def claim_ready_trigger_invocations(now: datetime) -> tuple[dict[uuid.UUID, list[AgentTrigger]], set[uuid.UUID]]:
    fired_by_agent: dict[uuid.UUID, list[AgentTrigger]] = {}
    force_invoke_agents: set[uuid.UUID] = set()

    claimed_executions = await claim_pending_trigger_executions()
    if claimed_executions:
        await mark_base_triggers_fired([trigger.id for _execution, trigger in claimed_executions], now)

    for execution, trigger in claimed_executions:
        runtime_trigger = build_execution_runtime_trigger(trigger, execution)
        fired_by_agent.setdefault(trigger.agent_id, []).append(runtime_trigger)
        force_invoke_agents.add(trigger.agent_id)

    return fired_by_agent, force_invoke_agents
