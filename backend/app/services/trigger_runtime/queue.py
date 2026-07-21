"""Queue trigger executions for distributed workers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.trigger import AgentTrigger
from app.models.trigger_execution import TriggerExecution
from app.services.agent_runtime.config import decide_runtime_v2
from app.services.trigger_runtime.intake import (
    TriggerRuntimeIntakeError,
    enqueue_trigger_runtime,
    load_trigger_agent,
)


async def _existing_execution(
    db: AsyncSession,
    *,
    trigger_id: uuid.UUID,
    idempotency_key: str,
) -> TriggerExecution | None:
    result = await db.execute(
        select(TriggerExecution).where(
            TriggerExecution.trigger_id == trigger_id,
            TriggerExecution.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def _mark_trigger_fired(trigger: AgentTrigger, now: datetime) -> None:
    trigger.last_fired_at = now
    trigger.fire_count = (trigger.fire_count or 0) + 1
    if trigger.type == "once":
        trigger.is_enabled = False
    if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
        trigger.is_enabled = False


def _fail_runtime_execution(
    execution: TriggerExecution,
    error: TriggerRuntimeIntakeError,
    now: datetime,
) -> None:
    execution.status = "failed"
    execution.finished_at = now
    execution.lease_owner = None
    execution.lease_expires_at = None
    execution.last_error = f"{error.code}: {error}"[:2000]


async def enqueue_trigger_execution(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    source: str,
    idempotency_key: str,
    payload_text: str = "",
    payload_obj: dict | None = None,
) -> tuple[TriggerExecution | None, bool]:
    """Atomically insert an occurrence and its required Runtime command."""
    normalized_key = idempotency_key[:255]
    now = datetime.now(timezone.utc)
    execution = TriggerExecution(
        id=uuid.uuid4(),
        trigger_id=trigger.id,
        agent_id=trigger.agent_id,
        source=source,
        status="pending",
        idempotency_key=normalized_key,
        payload=payload_obj if isinstance(payload_obj, dict) else {},
        payload_text=payload_text[:8000],
        scheduled_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(execution)
            await db.flush()
    except IntegrityError:
        existing = await _existing_execution(
            db,
            trigger_id=trigger.id,
            idempotency_key=normalized_key,
        )
        if existing is None:
            raise
        return None, False

    stored_result = await db.execute(
        select(AgentTrigger)
        .where(AgentTrigger.id == trigger.id)
        .with_for_update()
    )
    stored_trigger = stored_result.scalar_one_or_none()
    if stored_trigger is None:
        raise TriggerRuntimeIntakeError(
            "trigger_not_found",
            "Trigger disappeared while its execution was being registered",
        )
    if not stored_trigger.is_enabled:
        _fail_runtime_execution(
            execution,
            TriggerRuntimeIntakeError(
                "trigger_disabled",
                "Trigger was disabled before its execution was accepted",
            ),
            now,
        )
        await db.commit()
        return execution, True
    agent: Agent | None = await load_trigger_agent(db, trigger=stored_trigger)
    if agent is None:
        _fail_runtime_execution(
            execution,
            TriggerRuntimeIntakeError(
                "agent_not_found",
                "Runtime Trigger Agent does not exist",
            ),
            now,
        )
    else:
        try:
            async with db.begin_nested():
                handle = await enqueue_trigger_runtime(
                    db,
                    execution=execution,
                    trigger=stored_trigger,
                    agent=agent,
                )
                if handle is None:
                    decision = decide_runtime_v2(
                        agent_id=stored_trigger.agent_id,
                        source_type="trigger",
                    )
                    raise TriggerRuntimeIntakeError(
                        "runtime_v2_disabled",
                        f"Unified Runtime is required for Trigger execution ({decision.reason})",
                    )
                _mark_trigger_fired(stored_trigger, now)
                await db.flush()
        except TriggerRuntimeIntakeError as error:
            _fail_runtime_execution(execution, error, now)

    await db.commit()
    return execution, True


async def enqueue_webhook_execution(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    body: bytes,
    payload_text: str,
    payload_obj: dict | None,
    request_headers: dict[str, str],
) -> tuple[TriggerExecution | None, bool]:
    """Insert a webhook execution record.

    Returns `(execution, created)` where `created=False` means an identical
    idempotency key already exists and the event should be treated as a no-op.
    """
    delivery_key = (
        request_headers.get("x-idempotency-key")
        or request_headers.get("x-github-delivery")
        or request_headers.get("x-request-id")
        or request_headers.get("x-event-id")
        or hashlib.sha256(body).hexdigest()
    )[:255]

    return await enqueue_trigger_execution(
        db,
        trigger=trigger,
        source="webhook",
        idempotency_key=delivery_key,
        payload_text=payload_text,
        payload_obj=payload_obj,
    )
