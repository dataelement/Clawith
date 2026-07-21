"""Polling stream over stable product events, never checkpoint internals."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
import asyncio
from copy import deepcopy
import math
from typing import cast

from sqlalchemy import and_, or_, select

from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.contracts import (
    RunHandle,
    RuntimeEvent,
    RuntimeEventCursor,
    RuntimeEventType,
)
from app.services.agent_runtime.state import JsonObject, JsonValue


_TERMINAL_EVENT_TYPES = frozenset({"run_completed", "run_failed", "run_cancelled"})
_DELIVERY_EVENT_TYPES = frozenset({"delivery_succeeded", "delivery_failed"})
_SETTLED_DELIVERY_STATUSES = frozenset({"not_required", "delivered", "failed"})
_EVENT_TYPES = frozenset(
    {
        "run_created",
        "status_changed",
        "waiting_started",
        "resumed",
        "evidence_added",
        "verification_updated",
        *_TERMINAL_EVENT_TYPES,
        *_DELIVERY_EVENT_TYPES,
    }
)


class RuntimeEventStreamError(RuntimeError):
    """A stable Run event stream cannot be opened or decoded safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _event_statement(
    handle: RunHandle,
    *,
    after: RuntimeEventCursor | None,
    batch_size: int,
):
    statement = select(AgentRunEvent).where(
        AgentRunEvent.tenant_id == handle.tenant_id,
        AgentRunEvent.run_id == handle.run_id,
    )
    if after is not None:
        statement = statement.where(
            or_(
                AgentRunEvent.created_at > after.created_at,
                and_(
                    AgentRunEvent.created_at == after.created_at,
                    AgentRunEvent.id > after.event_id,
                ),
            )
        )
    return statement.order_by(AgentRunEvent.created_at.asc(), AgentRunEvent.id.asc()).limit(
        batch_size
    )


def _json_value(value: object, *, field: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return deepcopy(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeEventStreamError(
                "invalid_runtime_event",
                f"{field} contains a non-finite number",
            )
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise RuntimeEventStreamError(
                    "invalid_runtime_event",
                    f"{field} contains a non-string key",
                )
            copied[key] = _json_value(nested, field=field)
        return copied
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item, field=field) for item in value]
    raise RuntimeEventStreamError(
        "invalid_runtime_event",
        f"{field} is not JSON serializable",
    )


def _runtime_event(row: AgentRunEvent) -> RuntimeEvent:
    if row.event_type not in _EVENT_TYPES:
        raise RuntimeEventStreamError(
            "invalid_runtime_event_type",
            f"unsupported Runtime event type {row.event_type!r}",
        )
    payload = _json_value(row.payload, field="payload")
    artifact_refs = _json_value(row.artifact_refs, field="artifact_refs")
    if not isinstance(payload, dict) or not isinstance(artifact_refs, list):
        raise RuntimeEventStreamError(
            "invalid_runtime_event",
            "Runtime event payload or artifact_refs has the wrong shape",
        )
    enriched: JsonObject = {
        **payload,
        "summary": row.summary,
        "artifact_refs": artifact_refs,
    }
    return RuntimeEvent(
        tenant_id=row.tenant_id,
        run_id=row.run_id,
        event_id=row.id,
        event_type=cast(RuntimeEventType, row.event_type),
        payload=enriched,
        checkpoint_id=row.source_checkpoint_id,
        created_at=row.created_at,
    )


class DatabaseRuntimeEventStream:
    """Yield ordered AgentRunEvents through short-lived read sessions."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        poll_interval_seconds: float = 0.25,
        batch_size: int = 100,
    ) -> None:
        if poll_interval_seconds <= 0 or batch_size <= 0:
            raise ValueError("event stream polling settings must be positive")
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size

    @staticmethod
    def _validate_handle(handle: RunHandle) -> None:
        if handle.runtime_type != "langgraph" or not handle.thread_id.strip():
            raise RuntimeEventStreamError(
                "runtime_identity_mismatch",
                "event stream handle is not a valid LangGraph Run identity",
            )

    async def _require_run(self, handle: RunHandle) -> AgentRun:
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentRun).where(
                    AgentRun.tenant_id == handle.tenant_id,
                    AgentRun.id == handle.run_id,
                )
            )
            run = result.scalar_one_or_none()
        if run is None:
            raise RuntimeEventStreamError(
                "run_not_found",
                "event stream Run does not exist in its tenant",
            )
        if (
            run.runtime_type != "langgraph"
            or run.runtime_thread_id != handle.thread_id
        ):
            raise RuntimeEventStreamError(
                "runtime_identity_mismatch",
                "event stream handle does not match the stored LangGraph Run identity",
            )
        return run

    async def stream_run(
        self,
        handle: RunHandle,
        *,
        after: RuntimeEventCursor | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        self._validate_handle(handle)
        await self._require_run(handle)
        cursor = after
        terminal_seen = False

        while True:
            async with self._session_factory() as db:
                events_result = await db.execute(
                    _event_statement(
                        handle,
                        after=cursor,
                        batch_size=self._batch_size,
                    )
                )
                rows = list(events_result.scalars().all())
                status_result = await db.execute(
                    select(AgentRun.delivery_status).where(
                        AgentRun.tenant_id == handle.tenant_id,
                        AgentRun.id == handle.run_id,
                    )
                )
                delivery_status = status_result.scalar_one_or_none()
            if delivery_status is None:
                raise RuntimeEventStreamError(
                    "run_not_found",
                    "event stream Run disappeared from its tenant",
                )

            delivery_event_seen = False
            for row in rows:
                event = _runtime_event(row)
                if event.created_at is None or event.event_id is None:
                    raise RuntimeEventStreamError(
                        "invalid_runtime_event_position",
                        "persisted Runtime event has no reconnect position",
                    )
                cursor = RuntimeEventCursor(event.created_at, event.event_id)
                terminal_seen = terminal_seen or event.event_type in _TERMINAL_EVENT_TYPES
                delivery_event_seen = delivery_event_seen or event.event_type in _DELIVERY_EVENT_TYPES
                yield event

            if terminal_seen and (
                delivery_event_seen or delivery_status in _SETTLED_DELIVERY_STATUSES
            ):
                return
            await asyncio.sleep(self._poll_interval_seconds)


__all__ = [
    "DatabaseRuntimeEventStream",
    "RuntimeEventStreamError",
]
