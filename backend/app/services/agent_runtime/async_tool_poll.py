"""Durable timer scheduling for declared asynchronous Tool operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
import uuid

from sqlalchemy import false, func, select

from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.persistence import enqueue_resume


AsyncToolPollStatus = Literal["idle", "deferred", "scheduled"]


@dataclass(frozen=True, slots=True)
class AsyncToolPollResult:
    """One bounded scheduler iteration."""

    status: AsyncToolPollStatus
    execution_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _due_at(metadata: object) -> datetime | None:
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("async_poll_due_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _poll_schedule(
    execution: AgentToolExecution,
    *,
    now: datetime,
) -> tuple[datetime, str, str, str, str, dict] | None:
    metadata = execution.result_metadata
    if not isinstance(metadata, Mapping):
        return None
    operation = metadata.get("async_operation")
    if not isinstance(operation, Mapping) or operation.get("version") != 1:
        return None
    operation_key = operation.get("operation_key")
    poll = operation.get("poll")
    if not isinstance(operation_key, str) or not operation_key:
        return None
    if not isinstance(poll, Mapping):
        return None
    tool_name = poll.get("tool")
    arguments = poll.get("arguments")
    interval_ms = poll.get("interval_ms")
    if (
        not isinstance(tool_name, str)
        or not tool_name.strip()
        or not isinstance(arguments, Mapping)
        or isinstance(interval_ms, bool)
        or not isinstance(interval_ms, int)
        or interval_ms < 0
        or interval_ms > 600_000
    ):
        return None

    due_at = _due_at(metadata)
    if due_at is None:
        updated_at = execution.updated_at
        base = updated_at.astimezone(UTC) if updated_at and updated_at.tzinfo else now
        due_at = base + timedelta(milliseconds=interval_ms)
    correlation_id = metadata.get("async_poll_correlation_id")
    if not isinstance(correlation_id, str) or not correlation_id:
        # Receipts written before the durable scheduler used the generic
        # reconciliation wait correlation in the committed LangGraph state.
        correlation_id = f"tool-reconcile:{execution.run_id}"
    poll_call_id = metadata.get("async_poll_call_id")
    if not isinstance(poll_call_id, str) or not poll_call_id:
        poll_call_id = f"async-poll:{execution.id}"
    return (
        due_at,
        operation_key,
        correlation_id,
        poll_call_id,
        tool_name,
        dict(arguments),
    )


class AsyncToolPollScheduler:
    """Turn due async receipts into idempotent LangGraph timer resumes."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        clock: Callable[[], datetime] | None = None,
        scan_batch_size: int = 64,
    ) -> None:
        if scan_batch_size <= 0:
            raise ValueError("scan_batch_size must be positive")
        self._session_factory = session_factory
        self._clock = clock or _utc_now
        self._scan_batch_size = scan_batch_size

    async def run_once(self) -> AsyncToolPollResult:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("async poll clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentToolExecution)
                    .where(
                        AgentToolExecution.status == "started",
                        AgentToolExecution.result_metadata[
                            "runtime_async_pending"
                        ].as_boolean().is_(True),
                        func.coalesce(
                            AgentToolExecution.result_metadata[
                                "async_poll_scheduled"
                            ].as_boolean(),
                            false(),
                        ).is_(False),
                    )
                    .order_by(
                        AgentToolExecution.updated_at.asc(),
                        AgentToolExecution.id.asc(),
                    )
                    .limit(self._scan_batch_size)
                    .with_for_update(skip_locked=True)
                )
                candidates = list(result.scalars().all())
                eligible: list[
                    tuple[
                        datetime,
                        AgentToolExecution,
                        tuple[datetime, str, str, str, str, dict],
                    ]
                ] = []
                deferred = False
                normalized = False
                for execution in candidates:
                    metadata = execution.result_metadata
                    if not isinstance(metadata, Mapping):
                        continue
                    if metadata.get("async_poll_scheduled") is True:
                        continue
                    schedule = _poll_schedule(execution, now=now)
                    if schedule is None:
                        continue
                    due_at, _, correlation_id, poll_call_id, _, _ = schedule
                    if (
                        metadata.get("async_poll_due_at") != due_at.isoformat()
                        or metadata.get("async_poll_correlation_id") != correlation_id
                        or metadata.get("async_poll_call_id") != poll_call_id
                        or metadata.get("async_poll_scheduled") is not False
                    ):
                        execution.result_metadata = {
                            **dict(metadata),
                            "async_poll_due_at": due_at.isoformat(),
                            "async_poll_correlation_id": correlation_id,
                            "async_poll_call_id": poll_call_id,
                            "async_poll_scheduled": False,
                        }
                        normalized = True
                    if due_at > now:
                        deferred = True
                        continue
                    eligible.append((due_at, execution, schedule))
                if not eligible:
                    if normalized:
                        await db.flush()
                    return AsyncToolPollResult(
                        status="deferred" if deferred else "idle"
                    )

                _, execution, schedule = min(
                    eligible,
                    key=lambda item: (item[0], item[1].id),
                )
                (
                    _,
                    operation_key,
                    correlation_id,
                    poll_call_id,
                    poll_tool_name,
                    poll_arguments,
                ) = schedule
                await enqueue_resume(
                    db,
                    tenant_id=execution.tenant_id,
                    run_id=execution.run_id,
                    payload={
                        "resume_type": "timer",
                        "correlation_id": correlation_id,
                        "payload": {
                            "operation_key": operation_key,
                            "tool_call_id": execution.tool_call_id,
                            "poll_call_id": poll_call_id,
                            "poll": {
                                "tool": poll_tool_name,
                                "arguments": poll_arguments,
                            },
                        },
                    },
                    idempotency_key=f"async-poll:{execution.id}",
                )
                execution.result_metadata = {
                    **dict(execution.result_metadata),
                    "async_poll_scheduled": True,
                }
                await db.flush()
                return AsyncToolPollResult(
                    status="scheduled",
                    execution_id=execution.id,
                    run_id=execution.run_id,
                )


__all__ = [
    "AsyncToolPollResult",
    "AsyncToolPollScheduler",
]
