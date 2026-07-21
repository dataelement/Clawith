"""Transaction-scoped heartbeat intake for the durable Agent Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.config import decide_runtime_v2
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand


class HeartbeatRuntimeIntakeError(RuntimeError):
    """A heartbeat selected for Runtime v2 cannot be registered safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def heartbeat_source_execution_id(
    agent_id: uuid.UUID,
    occurrence_at: datetime,
) -> str:
    """Build a stable identity from the atomically claimed heartbeat slot."""
    if occurrence_at.tzinfo is None or occurrence_at.utcoffset() is None:
        raise HeartbeatRuntimeIntakeError(
            "invalid_heartbeat_occurrence",
            "Heartbeat occurrence timestamp must be timezone-aware",
        )
    timestamp = (
        occurrence_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    return f"heartbeat:{agent_id}:{timestamp}"


def schedule_occurrence_id(
    schedule_id: uuid.UUID,
    occurrence_at: datetime,
) -> uuid.UUID:
    """Derive one stable identity for an automatically claimed cron slot."""
    if occurrence_at.tzinfo is None or occurrence_at.utcoffset() is None:
        raise HeartbeatRuntimeIntakeError(
            "invalid_schedule_occurrence",
            "Schedule occurrence timestamp must be timezone-aware",
        )
    timestamp = (
        occurrence_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    return uuid.uuid5(schedule_id, f"schedule-occurrence:{timestamp}")


def _require_background_agent(agent: Agent, *, mode: str) -> uuid.UUID:
    if agent.tenant_id is None:
        raise HeartbeatRuntimeIntakeError(
            "agent_tenant_missing",
            f"Runtime {mode} Agent has no tenant",
        )
    if agent.primary_model_id is None:
        raise HeartbeatRuntimeIntakeError(
            "agent_model_missing",
            f"Runtime {mode} Agent has no primary model",
        )
    if agent.is_expired or agent.status not in {"creating", "running", "idle"}:
        raise HeartbeatRuntimeIntakeError(
            "agent_unavailable",
            f"Runtime {mode} Agent is unavailable",
        )
    return agent.tenant_id


async def enqueue_heartbeat_runtime(
    db: AsyncSession,
    *,
    agent: Agent,
    occurrence_at: datetime,
    instruction: str,
    context: Mapping[str, object] | None = None,
    settings_override: Settings | None = None,
) -> RunHandle | None:
    """Register one claimed heartbeat in the caller transaction when v2 is selected."""
    runtime_settings = settings_override or get_settings()
    decision = decide_runtime_v2(
        agent_id=agent.id,
        source_type="heartbeat",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None
    tenant_id = _require_background_agent(agent, mode="Heartbeat")
    normalized_instruction = instruction.strip()
    if not normalized_instruction:
        raise HeartbeatRuntimeIntakeError(
            "heartbeat_instruction_missing",
            "Runtime Heartbeat instruction is empty",
        )

    source_execution_id = heartbeat_source_execution_id(agent.id, occurrence_at)
    return await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=tenant_id,
            agent_id=agent.id,
            source_type="heartbeat",
            source_id=str(agent.id),
            source_execution_id=source_execution_id,
            goal=normalized_instruction,
            run_kind="background",
            model_id=agent.primary_model_id,
            delivery_status="not_required",
            idempotency_key=f"start:{source_execution_id}",
            payload={
                "background_mode": "heartbeat",
                "heartbeat_occurrence_at": occurrence_at.astimezone(UTC).isoformat(),
                "heartbeat_context": dict(context or {}),
            },
            origin_user_id=agent.creator_id,
        )
    )


async def enqueue_oneshot_runtime(
    db: AsyncSession,
    *,
    agent: Agent,
    prompt: str,
    occurrence_id: uuid.UUID,
    triggered_by_user_id: uuid.UUID | None,
    requested_model_turn_limit: int,
    settings_override: Settings | None = None,
) -> RunHandle | None:
    """Register one explicit background task without an entrypoint tool loop."""
    runtime_settings = settings_override or get_settings()
    decision = decide_runtime_v2(
        agent_id=agent.id,
        source_type="heartbeat",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None
    tenant_id = _require_background_agent(agent, mode="oneshot")
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise HeartbeatRuntimeIntakeError(
            "oneshot_prompt_missing",
            "Runtime oneshot prompt is empty",
        )
    if (
        isinstance(requested_model_turn_limit, bool)
        or not isinstance(requested_model_turn_limit, int)
        or requested_model_turn_limit <= 0
    ):
        raise HeartbeatRuntimeIntakeError(
            "oneshot_step_limit_invalid",
            "Runtime oneshot requested step limit must be positive",
        )
    source_execution_id = f"oneshot:{agent.id}:{occurrence_id}"
    return await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=tenant_id,
            agent_id=agent.id,
            source_type="heartbeat",
            source_id=str(agent.id),
            source_execution_id=source_execution_id,
            goal=normalized_prompt,
            run_kind="background",
            model_id=agent.primary_model_id,
            requested_model_turn_limit=requested_model_turn_limit,
            delivery_status="not_required",
            idempotency_key=f"start:{source_execution_id}",
            payload={
                "background_mode": "oneshot",
                "oneshot_occurrence_id": str(occurrence_id),
                "oneshot_prompt": normalized_prompt,
                "triggered_by_user_id": (
                    str(triggered_by_user_id)
                    if triggered_by_user_id is not None
                    else None
                ),
                "agent_name": agent.name,
            },
            origin_user_id=triggered_by_user_id or agent.creator_id,
            actor_user_id=triggered_by_user_id,
        )
    )


async def enqueue_schedule_runtime(
    db: AsyncSession,
    *,
    agent: Agent,
    schedule_id: uuid.UUID,
    occurrence_id: uuid.UUID,
    instruction: str,
    settings_override: Settings | None = None,
) -> RunHandle | None:
    """Register one cron schedule occurrence on the shared background Runtime."""
    runtime_settings = settings_override or get_settings()
    decision = decide_runtime_v2(
        agent_id=agent.id,
        source_type="heartbeat",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None
    tenant_id = _require_background_agent(agent, mode="schedule")
    normalized_instruction = instruction.strip()
    if not normalized_instruction:
        raise HeartbeatRuntimeIntakeError(
            "schedule_instruction_missing",
            "Runtime schedule instruction is empty",
        )
    source_execution_id = f"schedule:{schedule_id}:{occurrence_id}"
    return await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=tenant_id,
            agent_id=agent.id,
            source_type="heartbeat",
            source_id=str(schedule_id),
            source_execution_id=source_execution_id,
            goal=f"[自动调度任务] {normalized_instruction}",
            run_kind="background",
            model_id=agent.primary_model_id,
            delivery_status="not_required",
            idempotency_key=f"start:{source_execution_id}",
            payload={
                "background_mode": "schedule",
                "schedule_id": str(schedule_id),
                "schedule_occurrence_id": str(occurrence_id),
                "schedule_instruction": normalized_instruction,
            },
            origin_user_id=agent.creator_id,
        )
    )


__all__ = [
    "HeartbeatRuntimeIntakeError",
    "enqueue_heartbeat_runtime",
    "enqueue_oneshot_runtime",
    "enqueue_schedule_runtime",
    "heartbeat_source_execution_id",
    "schedule_occurrence_id",
]
