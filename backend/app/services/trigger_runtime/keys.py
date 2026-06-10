"""Deterministic idempotency keys for trigger executions."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from croniter import croniter

from app.models.trigger import AgentTrigger


def build_scheduled_execution_key(trigger: AgentTrigger, now: datetime) -> str:
    """Build a deterministic idempotency key for non-webhook trigger runs."""
    cfg = trigger.config or {}
    trigger_type = trigger.type

    if trigger_type == "once":
        return f"once:{trigger.id}:{cfg.get('at', '')}"

    if trigger_type == "interval":
        minutes = int(cfg.get("minutes", 30) or 30)
        base = trigger.last_fired_at or trigger.created_at
        due_at = base + timedelta(minutes=minutes)
        return f"interval:{trigger.id}:{due_at.astimezone(timezone.utc).isoformat()}"

    if trigger_type == "cron":
        expr = cfg.get("expr", "* * * * *")
        base = trigger.last_fired_at or trigger.created_at
        cron = croniter(expr, base)
        due_at = cron.get_next(datetime)
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        return f"cron:{trigger.id}:{due_at.astimezone(timezone.utc).isoformat()}"

    if trigger_type == "on_message":
        matched_from = str(cfg.get("_matched_from") or "")
        matched_message = str(cfg.get("_matched_message") or "")
        digest = hashlib.sha256(f"{matched_from}\n{matched_message}".encode("utf-8")).hexdigest()
        return f"on_message:{trigger.id}:{digest}"

    if trigger_type == "poll":
        current_value = str(cfg.get("_last_value") or "")
        digest = hashlib.sha256(current_value.encode("utf-8")).hexdigest()
        return f"poll:{trigger.id}:{digest}"

    return f"{trigger_type}:{trigger.id}:{now.replace(microsecond=0).isoformat()}"
