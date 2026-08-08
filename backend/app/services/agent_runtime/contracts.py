"""Stable product-facing contracts for the durable Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
import uuid

from app.services.agent_runtime.state import JsonObject, LifecycleStatus


RuntimeSourceType = Literal["chat", "trigger", "task", "a2a", "heartbeat"]
RunKind = Literal["foreground", "background", "delegated", "orchestration"]
RuntimeType = Literal["legacy", "langgraph"]
DeliveryStatus = Literal["not_required", "pending", "delivered", "failed"]
RUNTIME_COMMAND_METADATA_KEY = "__clawith_runtime"
RuntimeEventType = Literal[
    "run_created",
    "status_changed",
    "waiting_started",
    "resumed",
    "evidence_added",
    "verification_updated",
    "run_completed",
    "run_failed",
    "run_cancelled",
    "delivery_succeeded",
    "delivery_failed",
]


@dataclass(frozen=True, slots=True)
class StartRunCommand:
    """One product input that must create exactly one Run and start command."""

    tenant_id: uuid.UUID
    source_type: RuntimeSourceType
    goal: str
    run_kind: RunKind
    idempotency_key: str
    payload: JsonObject = field(default_factory=dict)
    agent_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    source_id: str | None = None
    source_execution_id: str | None = None
    correlation_id: str | None = None
    origin_user_id: uuid.UUID | None = None
    origin_agent_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    root_run_id: uuid.UUID | None = None
    system_role: str | None = None
    model_id: uuid.UUID | None = None
    runtime_thread_id: str | None = None
    requested_model_turn_limit: int | None = None
    scheduling_lane_key: str | None = None
    scheduling_position_created_at: datetime | None = None
    scheduling_position_id: uuid.UUID | None = None
    delivery_status: DeliveryStatus = "not_required"
    delivery_target: JsonObject | None = None
    actor_user_id: uuid.UUID | None = None
    actor_agent_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ResumeRunCommand:
    """An explicit input for an existing Run thread."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    idempotency_key: str
    payload: JsonObject
    actor_user_id: uuid.UUID | None = None
    actor_agent_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CancelRunCommand:
    """A cooperative cancellation request for an existing Run thread."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    idempotency_key: str
    reason: str | None = None
    actor_user_id: uuid.UUID | None = None
    actor_agent_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class RunHandle:
    """Stable identity returned after a Runtime command is durably accepted."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    thread_id: str
    command_id: uuid.UUID
    runtime_type: RuntimeType
    created: bool


@dataclass(frozen=True, slots=True)
class RunView:
    """Typed view derived from one exact Run/Command checkpoint."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    thread_id: str
    session_id: uuid.UUID | None
    source_type: RuntimeSourceType
    run_kind: RunKind
    goal: str
    runtime_type: RuntimeType
    execution_status: LifecycleStatus | None
    current_node: str | None
    model_step_count: int
    waiting_type: str | None
    waiting_reason: str | None
    waiting_correlation_id: str | None
    result_summary: str | None
    error_code: str | None
    last_error: str | None
    verification_result: JsonObject | None
    delivery_status: DeliveryStatus
    applied_checkpoint_id: str | None
    checkpoint_created_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Stable product event emitted independently of checkpoint internals."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    event_id: uuid.UUID | None
    event_type: RuntimeEventType
    payload: JsonObject = field(default_factory=dict)
    checkpoint_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEventCursor:
    """Reconnect position ordered by the product event's full identity."""

    created_at: datetime
    event_id: uuid.UUID
