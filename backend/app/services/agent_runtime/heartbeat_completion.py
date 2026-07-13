"""Idempotent heartbeat activity projection from terminal Runtime checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
import uuid

from sqlalchemy import select

from app.models.activity_log import AgentActivityLog
from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)


class HeartbeatRuntimeCompletionError(RuntimeError):
    """A completed heartbeat Run cannot be projected safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _activity_id(run_id: uuid.UUID, checkpoint_id: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"heartbeat-terminal:{checkpoint_id}")


def _is_heartbeat_ok(answer: str) -> bool:
    return "HEARTBEAT_OK" in answer.upper().replace(" ", "_")


class HeartbeatRuntimeCompletionHandler:
    """Append one visible activity only when a heartbeat reports useful work."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        if run.registry.source_type != "heartbeat":
            return
        lifecycle = checkpoint.state["lifecycle"]
        if lifecycle["status"] != "completed":
            return
        answer = lifecycle.get("final_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise HeartbeatRuntimeCompletionError(
                "missing_heartbeat_result",
                "completed heartbeat checkpoint has no final answer",
            )
        answer = answer.strip()
        if _is_heartbeat_ok(answer):
            return
        try:
            agent_id = uuid.UUID(run.registry.agent_id or "")
        except ValueError as exc:
            raise HeartbeatRuntimeCompletionError(
                "invalid_heartbeat_agent",
                "heartbeat Run has no valid Agent identity",
            ) from exc

        activity_id = _activity_id(run.run_id, checkpoint.checkpoint_id)
        async with self._session_factory() as db:
            async with db.begin():
                run_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                        AgentRun.source_type == "heartbeat",
                    )
                )
                stored_run = run_result.scalar_one_or_none()
                expected_source_execution_prefix = f"heartbeat:{agent_id}:"
                if (
                    stored_run is None
                    or stored_run.agent_id != agent_id
                    or stored_run.source_id != str(agent_id)
                    or stored_run.source_execution_id is None
                    or not stored_run.source_execution_id.startswith(
                        expected_source_execution_prefix
                    )
                ):
                    raise HeartbeatRuntimeCompletionError(
                        "heartbeat_source_mismatch",
                        "terminal heartbeat Run has inconsistent source identity",
                    )

                receipt_result = await db.execute(
                    select(AgentActivityLog.id).where(
                        AgentActivityLog.id == activity_id
                    )
                )
                if receipt_result.scalar_one_or_none() is not None:
                    return

                db.add(
                    AgentActivityLog(
                        id=activity_id,
                        agent_id=agent_id,
                        action_type="heartbeat",
                        summary=f"Heartbeat: {answer[:80]}",
                        detail_json={"reply": answer[:500]},
                        related_id=run.run_id,
                        created_at=self._clock(),
                    )
                )
                await db.flush()


__all__ = [
    "HeartbeatRuntimeCompletionError",
    "HeartbeatRuntimeCompletionHandler",
]
