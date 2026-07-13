"""Dispatch helpers for trigger executions."""

from __future__ import annotations

from datetime import datetime

from app.database import async_session
from app.models.trigger import AgentTrigger
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
