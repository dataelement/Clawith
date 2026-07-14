"""Checkpoint-authoritative projections for durable Agent runs.

The projector is deliberately one-way: it reads LangGraph state snapshots and
writes rebuildable product views. It never calls the graph, applies commands,
changes scheduling lanes, or uses projected status to decide execution.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from typing import Any, Protocol
import uuid

from langgraph.types import StateSnapshot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.checkpointer import runtime_thread_config


logger = logging.getLogger(__name__)

_LIFECYCLE_STATUSES = frozenset(
    {
        "created",
        "queued",
        "running",
        "waiting_user",
        "waiting_external",
        "waiting_agent",
        "verifying",
        "completed",
        "failed",
        "cancelled",
    }
)
_WAITING_STATUSES = frozenset({"waiting_user", "waiting_external", "waiting_agent"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_STARTED_STATUSES = _LIFECYCLE_STATUSES - {"created", "queued"}
_TERMINAL_EVENT_TYPES = {
    "completed": "run_completed",
    "failed": "run_failed",
    "cancelled": "run_cancelled",
}


class ProjectionError(RuntimeError):
    """A stable projector contract failed before product state was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProjectionNotFoundError(ProjectionError):
    """The tenant-scoped LangGraph Run registry row does not exist."""

    def __init__(self, message: str) -> None:
        super().__init__("run_not_found", message)


class ProjectionCheckpointError(ProjectionError):
    """Checkpoint identity, lineage, or state violates the Runtime contract."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_checkpoint", message)


class ProjectionHistoryGapError(ProjectionError):
    """The current projection watermark is absent from retained history."""

    def __init__(self, message: str) -> None:
        super().__init__("projection_history_gap", message)


class CheckpointStateSource(Protocol):
    """The read-only subset implemented by a compiled LangGraph graph."""

    def aget_state_history(
        self,
        config: dict[str, dict[str, str]],
    ) -> AsyncIterator[StateSnapshot]: ...


class CheckpointStateSourceResolver(Protocol):
    """Resolve the compiled checkpoint reader pinned on one Run Registry row."""

    def __call__(self, run: AgentRun) -> CheckpointStateSource: ...


@dataclass(frozen=True, slots=True)
class RuntimeProjectionTrigger:
    """Untrusted stream/event hint used only to wake checkpoint projection.

    The trigger never supplies lifecycle values. The projector validates it
    against authoritative checkpoint history and remains correct if it is
    duplicated, delayed, or stale.
    """

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    checkpoint_id: str
    checkpoint_version: int


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Observable result of one transaction-scoped projection attempt."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    applied_checkpoint_ids: tuple[str, ...]
    added_event_types: tuple[str, ...]
    authoritative_status: str
    authoritative_terminal: bool
    stale_trigger: bool = False
    history_gap: bool = False


@dataclass(frozen=True, slots=True)
class _CheckpointView:
    checkpoint_id: str
    checkpoint_version: int
    created_at: datetime
    parent_checkpoint_id: str | None
    status: str
    lifecycle: Mapping[str, Any]
    next_nodes: tuple[str, ...]
    interrupt_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EventSpec:
    checkpoint: _CheckpointView
    event_type: str
    summary: str
    payload: dict[str, Any]
    artifact_refs: list[Any]


def _mapping_field(value: object, field: str) -> object:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _require_text(
    value: object,
    *,
    field: str,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionCheckpointError(f"{field} must be a non-blank string")
    if max_length is not None and len(value) > max_length:
        raise ProjectionCheckpointError(f"{field} exceeds its {max_length}-character storage limit")
    return value


def _checkpoint_config_value(
    config: Mapping[str, Any] | None,
    field: str,
) -> object:
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    return configurable.get(field)


def _checkpoint_id(snapshot: StateSnapshot) -> str:
    return _require_text(
        _checkpoint_config_value(snapshot.config, "checkpoint_id"),
        field="checkpoint_id",
        max_length=255,
    )


def _checkpoint_thread_id(snapshot: StateSnapshot) -> str:
    return _require_text(
        _checkpoint_config_value(snapshot.config, "thread_id"),
        field="thread_id",
        max_length=255,
    )


def _checkpoint_version(snapshot: StateSnapshot) -> int:
    metadata = snapshot.metadata
    version = metadata.get("step") if isinstance(metadata, Mapping) else None
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProjectionCheckpointError("checkpoint metadata.step must be an integer version")
    return version


def _checkpoint_created_at(snapshot: StateSnapshot) -> datetime:
    raw_value = snapshot.created_at
    if not isinstance(raw_value, str):
        raise ProjectionCheckpointError("checkpoint created_at must be an ISO 8601 string")
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectionCheckpointError("checkpoint created_at must be valid ISO 8601") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _checkpoint_parent_id(snapshot: StateSnapshot) -> str | None:
    value = _checkpoint_config_value(snapshot.parent_config, "checkpoint_id")
    if value is None:
        return None
    return _require_text(value, field="parent checkpoint_id", max_length=255)


def _interrupt_ids(snapshot: StateSnapshot) -> tuple[str, ...]:
    values: list[str] = []
    for interrupt_value in snapshot.interrupts:
        interrupt_id = getattr(interrupt_value, "id", None)
        if interrupt_id is not None:
            values.append(str(interrupt_id))
    return tuple(values)


def _validate_registry(
    values: object,
    *,
    run: AgentRun,
) -> Mapping[str, Any]:
    if not isinstance(values, Mapping):
        raise ProjectionCheckpointError("checkpoint values must be an object")
    registry = values.get("registry")
    if registry is None:
        raise ProjectionCheckpointError("checkpoint registry is required")
    expected = {
        "tenant_id": str(run.tenant_id),
        "run_id": str(run.id),
        "graph_name": run.graph_name,
        "graph_version": run.graph_version,
    }
    mismatched = [
        field for field, expected_value in expected.items() if _mapping_field(registry, field) != expected_value
    ]
    if mismatched:
        raise ProjectionCheckpointError(
            "checkpoint registry does not match the tenant-scoped Run: " + ", ".join(sorted(mismatched))
        )
    return values


def _is_framework_initial_snapshot(snapshot: StateSnapshot, *, run: AgentRun) -> bool:
    """Recognize LangGraph's empty pre-run checkpoint without weakening validation.

    LangGraph persists an implementation checkpoint before the Runtime graph has
    written the product registry.  It is identifiable by its empty state and
    sole ``__start__`` continuation.  Product checkpoints, including any other
    empty or registry-less state, must still pass the normal strict validator.
    """

    if (
        not isinstance(snapshot.values, Mapping)
        or snapshot.values
        or tuple(str(node) for node in snapshot.next) != ("__start__",)
        or snapshot.parent_config is not None
        or snapshot.interrupts
    ):
        return False
    if _checkpoint_thread_id(snapshot) != run.runtime_thread_id:
        raise ProjectionCheckpointError("checkpoint thread_id does not match Run runtime_thread_id")
    # Validate the framework checkpoint's own identity even though it does not
    # participate in the product projection.
    _checkpoint_id(snapshot)
    _checkpoint_version(snapshot)
    _checkpoint_created_at(snapshot)
    return True


def _normalize_snapshot(snapshot: StateSnapshot, *, run: AgentRun) -> _CheckpointView:
    if _checkpoint_thread_id(snapshot) != run.runtime_thread_id:
        raise ProjectionCheckpointError("checkpoint thread_id does not match Run runtime_thread_id")
    values = _validate_registry(snapshot.values, run=run)
    lifecycle = values.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise ProjectionCheckpointError("checkpoint lifecycle must be an object")
    status = lifecycle.get("status")
    if status not in _LIFECYCLE_STATUSES:
        raise ProjectionCheckpointError(f"checkpoint lifecycle has unsupported status {status!r}")
    return _CheckpointView(
        checkpoint_id=_checkpoint_id(snapshot),
        checkpoint_version=_checkpoint_version(snapshot),
        created_at=_checkpoint_created_at(snapshot),
        parent_checkpoint_id=_checkpoint_parent_id(snapshot),
        status=str(status),
        lifecycle=lifecycle,
        next_nodes=tuple(str(node) for node in snapshot.next),
        interrupt_ids=_interrupt_ids(snapshot),
    )


async def _read_history(
    source: CheckpointStateSource,
    *,
    run: AgentRun,
) -> list[_CheckpointView]:
    if run.runtime_thread_id != str(run.id):
        raise ProjectionCheckpointError("Run runtime_thread_id must equal its Run ID")
    newest_first: list[_CheckpointView] = []
    async for snapshot in source.aget_state_history(runtime_thread_config(run.id)):
        if _is_framework_initial_snapshot(snapshot, run=run):
            continue
        newest_first.append(_normalize_snapshot(snapshot, run=run))
    if not newest_first:
        raise ProjectionCheckpointError("Run has no observable checkpoint")

    checkpoint_ids = [checkpoint.checkpoint_id for checkpoint in newest_first]
    if len(checkpoint_ids) != len(set(checkpoint_ids)):
        raise ProjectionCheckpointError("checkpoint history contains duplicate checkpoint IDs")

    oldest_first = list(reversed(newest_first))
    for previous, current in zip(oldest_first, oldest_first[1:], strict=False):
        if current.checkpoint_version <= previous.checkpoint_version:
            raise ProjectionCheckpointError("checkpoint versions must increase along the parent history")
        if current.parent_checkpoint_id is not None and current.parent_checkpoint_id != previous.checkpoint_id:
            raise ProjectionCheckpointError("checkpoint parent history does not form one linear Run chain")
    return oldest_first


def _json_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ProjectionCheckpointError(f"{field} must be JSON serializable") from exc


def _waiting_projection(
    checkpoint: _CheckpointView,
) -> tuple[str | None, str | None]:
    if checkpoint.status not in _WAITING_STATUSES:
        return None, None

    expected_type = checkpoint.status.removeprefix("waiting_")
    request = checkpoint.lifecycle.get("waiting_request")
    if request is not None and not isinstance(request, Mapping):
        raise ProjectionCheckpointError("waiting lifecycle waiting_request must be an object")
    request_mapping = request if isinstance(request, Mapping) else {}
    waiting_type = request_mapping.get("waiting_type", expected_type)
    if waiting_type != expected_type:
        raise ProjectionCheckpointError("waiting_request.waiting_type must match lifecycle status")
    reason = request_mapping.get(
        "reason",
        checkpoint.lifecycle.get("reason"),
    )
    return expected_type, _json_text(reason, field="waiting reason")


def _waiting_event_fields(checkpoint: _CheckpointView) -> dict[str, str]:
    """Expose only stable resume metadata required by product clients."""
    if checkpoint.status not in _WAITING_STATUSES:
        return {}
    request = checkpoint.lifecycle.get("waiting_request")
    if not isinstance(request, Mapping):
        return {}
    correlation_id = request.get("correlation_id")
    if correlation_id is None:
        return {}
    return {
        "correlation_id": _require_text(
            correlation_id,
            field="waiting_request.correlation_id",
            max_length=200,
        )
    }


def _result_projection(
    checkpoint: _CheckpointView,
) -> tuple[str | None, list[Any]]:
    result = checkpoint.lifecycle.get("result_summary")
    if result is None:
        return None, []
    artifact_refs: list[Any] = []
    if isinstance(result, Mapping):
        raw_artifact_refs = result.get("artifact_refs")
        if raw_artifact_refs is not None:
            if not isinstance(raw_artifact_refs, list):
                raise ProjectionCheckpointError("result_summary.artifact_refs must be a list")
            artifact_refs = list(raw_artifact_refs)
        summary = result.get("summary")
        if isinstance(summary, str):
            return summary, artifact_refs
    return _json_text(result, field="result_summary"), artifact_refs


def _error_projection(
    checkpoint: _CheckpointView,
) -> tuple[str | None, str | None]:
    error = checkpoint.lifecycle.get("error")
    if error is None:
        if checkpoint.status == "failed":
            return None, _json_text(
                checkpoint.lifecycle.get("reason"),
                field="failure reason",
            )
        return None, None
    if isinstance(error, Mapping):
        raw_code = error.get("code")
        error_code = (
            _require_text(
                raw_code,
                field="error.code",
                max_length=100,
            )
            if raw_code is not None
            else None
        )
        message = error.get("message")
        return error_code, _json_text(
            message if message is not None else error,
            field="error",
        )
    return None, _json_text(error, field="error")


def _checkpoint_payload(
    checkpoint: _CheckpointView,
    *,
    previous_status: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "checkpoint_version": checkpoint.checkpoint_version,
        "status": checkpoint.status,
    }
    if previous_status is not None:
        payload["previous_status"] = previous_status
    if checkpoint.next_nodes:
        payload["next"] = list(checkpoint.next_nodes)
    if checkpoint.interrupt_ids:
        payload["interrupt_ids"] = list(checkpoint.interrupt_ids)
    waiting_type, waiting_reason = _waiting_projection(checkpoint)
    if waiting_type is not None:
        payload["waiting_type"] = waiting_type
    if waiting_reason is not None:
        payload["waiting_reason"] = waiting_reason
    payload.update(_waiting_event_fields(checkpoint))
    error_code, _ = _error_projection(checkpoint)
    if error_code is not None:
        payload["error_code"] = error_code
    return payload


def _event_spec(
    checkpoint: _CheckpointView,
    event_type: str,
    summary: str,
    *,
    previous_status: str | None,
    artifact_refs: list[Any] | None = None,
) -> _EventSpec:
    return _EventSpec(
        checkpoint=checkpoint,
        event_type=event_type,
        summary=summary,
        payload=_checkpoint_payload(
            checkpoint,
            previous_status=previous_status,
        ),
        artifact_refs=list(artifact_refs or []),
    )


def _derive_events(
    previous: _CheckpointView | None,
    checkpoint: _CheckpointView,
) -> list[_EventSpec]:
    previous_status = previous.status if previous is not None else None
    event_specs: list[_EventSpec] = []
    if previous is None:
        event_specs.append(
            _event_spec(
                checkpoint,
                "run_created",
                f"Run entered {checkpoint.status}.",
                previous_status=None,
            )
        )
    elif previous_status != checkpoint.status:
        event_specs.append(
            _event_spec(
                checkpoint,
                "status_changed",
                f"Run status changed from {previous_status} to {checkpoint.status}.",
                previous_status=previous_status,
            )
        )

    if previous_status in _WAITING_STATUSES and checkpoint.status not in _WAITING_STATUSES:
        event_specs.append(
            _event_spec(
                checkpoint,
                "resumed",
                f"Run resumed from {previous_status}.",
                previous_status=previous_status,
            )
        )
    if checkpoint.status in _WAITING_STATUSES and previous_status != checkpoint.status:
        waiting_type, _ = _waiting_projection(checkpoint)
        event_specs.append(
            _event_spec(
                checkpoint,
                "waiting_started",
                f"Run is waiting for {waiting_type} input.",
                previous_status=previous_status,
            )
        )

    previous_verification = previous.lifecycle.get("verification_result") if previous is not None else None
    verification = checkpoint.lifecycle.get("verification_result")
    if verification is not None and verification != previous_verification:
        event_specs.append(
            _event_spec(
                checkpoint,
                "verification_updated",
                "Run verification state updated.",
                previous_status=previous_status,
            )
        )

    if checkpoint.status in _TERMINAL_STATUSES and previous_status != checkpoint.status:
        _, artifact_refs = _result_projection(checkpoint)
        event_specs.append(
            _event_spec(
                checkpoint,
                _TERMINAL_EVENT_TYPES[checkpoint.status],
                f"Run {checkpoint.status}.",
                previous_status=previous_status,
                artifact_refs=artifact_refs,
            )
        )
    return event_specs


def _event_id(
    run_id: uuid.UUID,
    *,
    checkpoint_id: str,
    event_type: str,
) -> uuid.UUID:
    return uuid.uuid5(run_id, f"{checkpoint_id}:{event_type}")


def _event_idempotency_key(
    *,
    checkpoint_id: str,
    event_type: str,
) -> str:
    checkpoint_digest = sha256(checkpoint_id.encode("utf-8")).hexdigest()
    return f"checkpoint:{checkpoint_digest}:{event_type}"


def _build_event(run: AgentRun, spec: _EventSpec) -> AgentRunEvent:
    return AgentRunEvent(
        id=_event_id(
            run.id,
            checkpoint_id=spec.checkpoint.checkpoint_id,
            event_type=spec.event_type,
        ),
        tenant_id=run.tenant_id,
        run_id=run.id,
        agent_id=run.agent_id,
        event_type=spec.event_type,
        summary=spec.summary,
        payload=dict(spec.payload),
        artifact_refs=list(spec.artifact_refs),
        idempotency_key=_event_idempotency_key(
            checkpoint_id=spec.checkpoint.checkpoint_id,
            event_type=spec.event_type,
        ),
        source_checkpoint_id=spec.checkpoint.checkpoint_id,
        created_at=spec.checkpoint.created_at,
    )


def _validate_trigger(
    trigger: RuntimeProjectionTrigger | None,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    history: list[_CheckpointView],
    watermark: str | None,
) -> bool:
    if trigger is None:
        return False
    if trigger.tenant_id != tenant_id or trigger.run_id != run_id:
        raise ProjectionCheckpointError("projection trigger tenant_id and run_id must match the requested Run")
    _require_text(
        trigger.checkpoint_id,
        field="trigger checkpoint_id",
        max_length=255,
    )
    if isinstance(trigger.checkpoint_version, bool) or not isinstance(
        trigger.checkpoint_version,
        int,
    ):
        raise ProjectionCheckpointError("trigger checkpoint_version must be an integer")

    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in history}
    triggered_checkpoint = by_id.get(trigger.checkpoint_id)
    if triggered_checkpoint is not None and triggered_checkpoint.checkpoint_version != trigger.checkpoint_version:
        raise ProjectionCheckpointError("projection trigger version does not match checkpoint history")

    latest = history[-1]
    if trigger.checkpoint_version > latest.checkpoint_version:
        raise ProjectionCheckpointError("projection trigger is newer than observable checkpoint history")
    if trigger.checkpoint_version == latest.checkpoint_version and trigger.checkpoint_id != latest.checkpoint_id:
        raise ProjectionCheckpointError("projection trigger conflicts with the latest checkpoint branch")

    watermark_checkpoint = by_id.get(watermark) if watermark is not None else None
    if watermark_checkpoint is not None:
        return trigger.checkpoint_version < watermark_checkpoint.checkpoint_version
    return trigger.checkpoint_version < latest.checkpoint_version


def _projection_started_at(
    run: AgentRun,
    *,
    previous: _CheckpointView | None,
    pending: list[_CheckpointView],
    rebuild: bool,
    history_gap: bool,
) -> datetime | None:
    if history_gap:
        return run.projected_started_at
    if not rebuild and run.projected_started_at is not None:
        return run.projected_started_at
    candidates = [checkpoint for checkpoint in ([previous] if previous else []) + pending]
    return next(
        (checkpoint.created_at for checkpoint in candidates if checkpoint.status in _STARTED_STATUSES),
        None,
    )


def _apply_projection_values(
    run: AgentRun,
    *,
    latest: _CheckpointView,
    previous: _CheckpointView | None,
    pending: list[_CheckpointView],
    rebuild: bool,
    history_gap: bool,
    projected_at: datetime,
) -> None:
    waiting_type, waiting_reason = _waiting_projection(latest)
    result_summary, _ = _result_projection(latest)
    error_code, last_error = _error_projection(latest)

    run.projected_execution_status = latest.status
    run.projected_waiting_type = waiting_type
    run.projected_waiting_reason = waiting_reason
    run.projected_result_summary = result_summary
    run.projected_error_code = error_code
    run.projected_last_error = last_error
    run.projected_checkpoint_id = latest.checkpoint_id
    run.projection_updated_at = projected_at
    run.projected_started_at = _projection_started_at(
        run,
        previous=previous,
        pending=pending,
        rebuild=rebuild,
        history_gap=history_gap,
    )
    run.projected_completed_at = latest.created_at if latest.status in _TERMINAL_STATUSES else None


class RuntimeProjector:
    """Project authoritative checkpoint history into tenant-scoped product rows."""

    def __init__(
        self,
        state_source: CheckpointStateSource | None = None,
        *,
        state_source_resolver: CheckpointStateSourceResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (state_source is None) == (state_source_resolver is None):
            raise ValueError("provide exactly one checkpoint state source or resolver")
        self._state_source = state_source
        self._state_source_resolver = state_source_resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def _source_for_run(self, run: AgentRun) -> CheckpointStateSource:
        if self._state_source_resolver is not None:
            return self._state_source_resolver(run)
        assert self._state_source is not None
        return self._state_source

    async def _lock_run(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> AgentRun:
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.id == run_id,
                AgentRun.runtime_type == "langgraph",
            )
            .with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ProjectionNotFoundError(f"LangGraph run {run_id} does not exist in tenant {tenant_id}")
        return run

    async def _existing_event_keys(
        self,
        db: AsyncSession,
        *,
        run: AgentRun,
        event_specs: list[_EventSpec],
    ) -> set[tuple[str, str]]:
        checkpoint_ids = {spec.checkpoint.checkpoint_id for spec in event_specs}
        if not checkpoint_ids:
            return set()
        result = await db.execute(
            select(
                AgentRunEvent.source_checkpoint_id,
                AgentRunEvent.event_type,
            ).where(
                AgentRunEvent.tenant_id == run.tenant_id,
                AgentRunEvent.run_id == run.id,
                AgentRunEvent.source_checkpoint_id.in_(checkpoint_ids),
            )
        )
        return {
            (str(source_checkpoint_id), str(event_type))
            for source_checkpoint_id, event_type in result.all()
            if source_checkpoint_id is not None
        }

    async def project_run(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        trigger: RuntimeProjectionTrigger | None = None,
        rebuild_latest_on_history_gap: bool = False,
    ) -> ProjectionResult:
        """Project one Run without committing the caller transaction.

        The Run row lock serializes projector attempts for this Run. Checkpoint
        history remains authoritative; the lock only protects the product-side
        watermark and event insert transaction.
        """
        run = await self._lock_run(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        history = await _read_history(self._source_for_run(run), run=run)
        watermark = run.projected_checkpoint_id
        stale_trigger = _validate_trigger(
            trigger,
            tenant_id=tenant_id,
            run_id=run_id,
            history=history,
            watermark=watermark,
        )

        history_gap = False
        suppress_events = False
        previous: _CheckpointView | None = None
        if watermark is None:
            pending = list(history)
            rebuild = True
        else:
            checkpoint_indexes = {checkpoint.checkpoint_id: index for index, checkpoint in enumerate(history)}
            watermark_index = checkpoint_indexes.get(watermark)
            if watermark_index is None:
                if not rebuild_latest_on_history_gap:
                    raise ProjectionHistoryGapError(
                        "current projected_checkpoint_id is absent from authoritative checkpoint history"
                    )
                history_gap = True
                suppress_events = True
                pending = [history[-1]]
                rebuild = True
                logger.warning(
                    "Runtime projection history gap; rebuilding latest snapshot "
                    "without lifecycle timeline tenant_id=%s run_id=%s "
                    "old_watermark=%s latest_checkpoint_id=%s",
                    tenant_id,
                    run_id,
                    watermark,
                    history[-1].checkpoint_id,
                )
            else:
                previous = history[watermark_index]
                pending = history[watermark_index + 1 :]
                rebuild = False

        latest = history[-1]
        if not pending:
            return ProjectionResult(
                tenant_id=tenant_id,
                run_id=run_id,
                applied_checkpoint_ids=(),
                added_event_types=(),
                authoritative_status=latest.status,
                authoritative_terminal=latest.status in _TERMINAL_STATUSES,
                stale_trigger=stale_trigger,
                history_gap=history_gap,
            )

        event_specs: list[_EventSpec] = []
        event_previous = previous
        if not suppress_events:
            for checkpoint in pending:
                event_specs.extend(_derive_events(event_previous, checkpoint))
                event_previous = checkpoint

        existing_event_keys = await self._existing_event_keys(
            db,
            run=run,
            event_specs=event_specs,
        )
        added_event_types: list[str] = []
        for spec in event_specs:
            event_key = (
                spec.checkpoint.checkpoint_id,
                spec.event_type,
            )
            if event_key in existing_event_keys:
                continue
            db.add(_build_event(run, spec))
            added_event_types.append(spec.event_type)

        projected_at = self._clock()
        if projected_at.tzinfo is None:
            projected_at = projected_at.replace(tzinfo=UTC)
        else:
            projected_at = projected_at.astimezone(UTC)
        _apply_projection_values(
            run,
            latest=pending[-1],
            previous=previous,
            pending=pending,
            rebuild=rebuild,
            history_gap=history_gap,
            projected_at=projected_at,
        )
        await db.flush()

        return ProjectionResult(
            tenant_id=tenant_id,
            run_id=run_id,
            applied_checkpoint_ids=tuple(checkpoint.checkpoint_id for checkpoint in pending),
            added_event_types=tuple(added_event_types),
            authoritative_status=pending[-1].status,
            authoritative_terminal=pending[-1].status in _TERMINAL_STATUSES,
            stale_trigger=stale_trigger,
            history_gap=history_gap,
        )
