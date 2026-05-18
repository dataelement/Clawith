"""Queue trigger executions for distributed workers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trigger import AgentTrigger
from app.models.trigger_execution import TriggerExecution


async def enqueue_trigger_execution(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
    source: str,
    idempotency_key: str,
    payload_text: str = "",
    payload_obj: dict | None = None,
) -> tuple[TriggerExecution | None, bool]:
    """Insert a generic trigger execution record."""
    execution = TriggerExecution(
        trigger_id=trigger.id,
        agent_id=trigger.agent_id,
        source=source,
        status="pending",
        idempotency_key=idempotency_key[:255],
        payload=payload_obj if isinstance(payload_obj, dict) else {},
        payload_text=payload_text[:8000],
        scheduled_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    try:
        await db.commit()
        return execution, True
    except IntegrityError:
        await db.rollback()
        return None, False


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
