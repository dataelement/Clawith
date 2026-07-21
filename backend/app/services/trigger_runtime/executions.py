"""Execution claiming and completion helpers for distributed triggers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, exists, or_, select

from app.config import get_settings
from app.database import async_session
from app.models.agent_run import AgentRun
from app.models.trigger import AgentTrigger
from app.models.trigger_execution import TriggerExecution

settings = get_settings()


async def claim_pending_trigger_executions(
    *,
    sources: list[str] | None = None,
    limit: int = 100,
) -> list[tuple[TriggerExecution, AgentTrigger]]:
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(minutes=5)
    claimed_pairs: list[tuple[TriggerExecution, AgentTrigger]] = []
    sources = sources or ["webhook", "cron", "once", "interval", "poll", "on_message"]
    async with async_session() as db:
        result = await db.execute(
            select(TriggerExecution, AgentTrigger)
            .join(AgentTrigger, AgentTrigger.id == TriggerExecution.trigger_id)
            .where(
                TriggerExecution.source.in_(sources),
                AgentTrigger.is_enabled.is_(True),
                ~exists(
                    select(AgentRun.id).where(
                        AgentRun.source_type == "trigger",
                        AgentRun.source_execution_id
                        == cast(TriggerExecution.id, String),
                    )
                ),
                or_(
                    TriggerExecution.status == "pending",
                    (TriggerExecution.status == "processing") & (
                        TriggerExecution.lease_expires_at.is_(None)
                        | (TriggerExecution.lease_expires_at < now)
                    ),
                ),
            )
            .order_by(TriggerExecution.scheduled_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        rows = result.all()
        for execution, trigger in rows:
            execution.status = "processing"
            execution.started_at = execution.started_at or now
            execution.finished_at = None
            execution.lease_owner = settings.INSTANCE_ID
            execution.lease_expires_at = lease_until
            claimed_pairs.append((execution, trigger))
        await db.commit()
        for execution, trigger in claimed_pairs:
            if execution in db:
                db.expunge(execution)
            if trigger in db:
                db.expunge(trigger)
    return claimed_pairs


def build_execution_runtime_trigger(trigger: AgentTrigger, execution: TriggerExecution) -> AgentTrigger:
    stored_config = trigger.config if isinstance(trigger.config, dict) else {}
    runtime_cfg = {
        **stored_config,
        "_execution_id": str(execution.id),
    }
    if execution.payload:
        runtime_cfg.update(execution.payload)
    if execution.payload_text:
        runtime_cfg["_webhook_payload"] = execution.payload_text
    return AgentTrigger(
        id=trigger.id,
        agent_id=trigger.agent_id,
        name=trigger.name,
        type=trigger.type,
        config=runtime_cfg,
        reason=trigger.reason,
        focus_ref=trigger.focus_ref,
        is_enabled=trigger.is_enabled,
        last_fired_at=trigger.last_fired_at,
        fire_count=trigger.fire_count,
        max_fires=trigger.max_fires,
        cooldown_seconds=trigger.cooldown_seconds,
        is_system=trigger.is_system,
        created_at=trigger.created_at,
        expires_at=trigger.expires_at,
    )


async def mark_base_triggers_fired(trigger_ids: list[uuid.UUID], now: datetime) -> None:
    if not trigger_ids:
        return
    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(AgentTrigger.id.in_(trigger_ids))
        )
        for trigger in result.scalars().all():
            trigger.last_fired_at = now
            trigger.fire_count += 1
            if trigger.type == "once":
                trigger.is_enabled = False
            if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
                trigger.is_enabled = False
        await db.commit()
