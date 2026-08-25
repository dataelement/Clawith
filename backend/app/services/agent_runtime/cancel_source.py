"""Checkpoint-aware cooperative cancellation backed by the Command Inbox."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select

from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.node_executor import CancelSignal
from app.services.agent_runtime.state import RuntimeContext, RuntimeGraphState
from app.services.agent_runtime.tool_contracts import ToolCancelCapability


class CancelPollSource(Protocol):
    async def get_cancel(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> CancelSignal | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeToolCancelToken:
    """Poll durable Run cancellation and describe adapter capability."""

    source: CancelPollSource
    state: RuntimeGraphState
    context: RuntimeContext
    capability: ToolCancelCapability

    async def poll(self) -> CancelSignal | None:
        return await self.source.get_cancel(self.state, self.context)

    def telemetry(self, signal: CancelSignal) -> dict[str, object]:
        return {
            "cancel_requested": True,
            "cancel_command_id": signal.command_id,
            "cancel_reason": signal.reason,
            "cancel_capability": self.capability,
            "cancel_propagation": (
                "cooperative_task_cancelled"
                if self.capability == "cooperative"
                else "stop_waiting_only"
            ),
        }


class RuntimeCancelSourceError(RuntimeError):
    """Checkpoint identity or a persisted cancel request is malformed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_scope(
    context: RuntimeContext,
) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return uuid.UUID(context.tenant_id), uuid.UUID(context.run_id)
    except ValueError as exc:
        raise RuntimeCancelSourceError(
            "invalid_runtime_identity",
            "Runtime tenant and Run identities must be UUIDs",
        ) from exc


def _cancel_signal(command: AgentRunCommand) -> CancelSignal:
    payload = command.payload
    if not isinstance(payload, Mapping):
        raise RuntimeCancelSourceError(
            "invalid_cancel_payload",
            "persisted cancel payload must be an object",
        )
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise RuntimeCancelSourceError(
            "invalid_cancel_payload",
            "persisted cancel reason must be a string when present",
        )
    return CancelSignal(
        command_id=str(command.id),
        reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
    )


class DatabaseRuntimeCancelSource:
    """Read durable cancellation without consulting product projections."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_cancel(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> CancelSignal | None:
        del state
        tenant_id, run_id = _require_scope(context)
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentRunCommand)
                .where(
                    AgentRunCommand.tenant_id == tenant_id,
                    AgentRunCommand.run_id == run_id,
                    AgentRunCommand.command_type == "cancel",
                    AgentRunCommand.status.in_(("pending", "claimed")),
                )
                .order_by(AgentRunCommand.created_at, AgentRunCommand.id)
            )
            for command in result.scalars().all():
                return _cancel_signal(command)
        return None


__all__ = [
    "DatabaseRuntimeCancelSource",
    "RuntimeCancelSourceError",
    "RuntimeToolCancelToken",
]
