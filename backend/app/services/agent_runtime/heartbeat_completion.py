"""Idempotent heartbeat activity projection from terminal Runtime checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Callable
import uuid

from sqlalchemy import select

from app.models.activity_log import AgentActivityLog
from app.models.agent_run import AgentRun
from app.models.notification import Notification
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


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_BACKGROUND_MODES = frozenset({"heartbeat", "schedule", "oneshot"})


def _effect_id(run_id: uuid.UUID, checkpoint_id: str, mode: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"{mode}-terminal:{checkpoint_id}")


def _is_heartbeat_ok(answer: str) -> bool:
    return "HEARTBEAT_OK" in answer.upper().replace(" ", "_")


def _mode(checkpoint: CheckpointObservation) -> str:
    initial_input = checkpoint.state["snapshots"].initial_input
    mode = initial_input.get("background_mode", "heartbeat")
    if not isinstance(mode, str) or mode not in _BACKGROUND_MODES:
        raise HeartbeatRuntimeCompletionError(
            "background_mode_invalid",
            "heartbeat-source Run has an unsupported background mode",
        )
    return str(mode)


def _answer(checkpoint: CheckpointObservation, *, mode: str) -> str:
    answer = checkpoint.state["lifecycle"].get("final_answer")
    if not isinstance(answer, str) or not answer.strip():
        raise HeartbeatRuntimeCompletionError(
            f"missing_{mode}_result",
            f"completed {mode} checkpoint has no final answer",
        )
    return answer.strip()


def _failure_code(checkpoint: CheckpointObservation) -> str:
    lifecycle = checkpoint.state["lifecycle"]
    error = lifecycle.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    reason = lifecycle.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    return str(lifecycle["status"])


def _require_source(
    stored_run: AgentRun | None,
    *,
    mode: str,
    agent_id: uuid.UUID,
    initial_input: Mapping[str, object],
) -> uuid.UUID | None:
    if stored_run is None or stored_run.source_execution_id is None:
        raise HeartbeatRuntimeCompletionError(
            f"{mode}_source_mismatch",
            f"terminal {mode} Run has inconsistent source identity",
        )
    related_id = None
    if mode == "heartbeat":
        valid = (
            stored_run.source_id == str(agent_id)
            and stored_run.source_execution_id.startswith(f"heartbeat:{agent_id}:")
        )
    elif mode == "oneshot":
        valid = (
            stored_run.source_id == str(agent_id)
            and stored_run.source_execution_id.startswith(f"oneshot:{agent_id}:")
        )
    else:
        raw_schedule_id = initial_input.get("schedule_id")
        try:
            related_id = uuid.UUID(str(raw_schedule_id))
        except (TypeError, ValueError):
            valid = False
        else:
            valid = (
                stored_run.source_id == str(related_id)
                and stored_run.source_execution_id.startswith(
                    f"schedule:{related_id}:"
                )
            )
    if not valid:
        raise HeartbeatRuntimeCompletionError(
            f"{mode}_source_mismatch",
            f"terminal {mode} Run has inconsistent source identity",
        )
    return related_id


class HeartbeatRuntimeCompletionHandler:
    """Project heartbeat-source background modes from terminal checkpoints."""

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
        if run.source_type != "heartbeat":
            return
        lifecycle = checkpoint.state["lifecycle"]
        status = lifecycle["status"]
        if status not in _TERMINAL_STATUSES:
            return
        mode = _mode(checkpoint)
        if mode in {"heartbeat", "schedule"} and status != "completed":
            return
        answer = _answer(checkpoint, mode=mode) if status == "completed" else None
        if mode == "heartbeat" and answer is not None and _is_heartbeat_ok(answer):
            return
        initial_input = checkpoint.state["snapshots"].initial_input
        if mode == "oneshot" and status == "completed":
            return
        raw_triggered_by = initial_input.get("triggered_by_user_id")
        if mode == "oneshot" and raw_triggered_by is None:
            return
        try:
            agent_id = uuid.UUID(run.agent_id or "")
        except ValueError as exc:
            raise HeartbeatRuntimeCompletionError(
                "invalid_heartbeat_agent",
                "heartbeat Run has no valid Agent identity",
            ) from exc

        effect_id = _effect_id(run.run_id, checkpoint.checkpoint_id, mode)
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
                if stored_run is None or stored_run.agent_id != agent_id:
                    raise HeartbeatRuntimeCompletionError(
                        f"{mode}_source_mismatch",
                        f"terminal {mode} Run has inconsistent Agent identity",
                    )
                related_id = _require_source(
                    stored_run,
                    mode=mode,
                    agent_id=agent_id,
                    initial_input=initial_input,
                )

                if mode == "oneshot":
                    try:
                        triggered_by = uuid.UUID(str(raw_triggered_by))
                    except (TypeError, ValueError) as exc:
                        raise HeartbeatRuntimeCompletionError(
                            "oneshot_user_invalid",
                            "terminal oneshot Run has no valid triggering user",
                        ) from exc
                    receipt_result = await db.execute(
                        select(Notification.id).where(Notification.id == effect_id)
                    )
                    if receipt_result.scalar_one_or_none() is not None:
                        return
                    agent_name = initial_input.get("agent_name")
                    safe_agent_name = (
                        agent_name.strip()
                        if isinstance(agent_name, str) and agent_name.strip()
                        else "Agent"
                    )
                    db.add(
                        Notification(
                            id=effect_id,
                            user_id=triggered_by,
                            type="system",
                            title=f"{safe_agent_name} task failed",
                            body=f"任务执行未完成（{_failure_code(checkpoint)}）",
                            link=f"/agents/{agent_id}#chat",
                            ref_id=agent_id,
                            sender_name=safe_agent_name,
                        )
                    )
                    await db.flush()
                    return

                receipt_result = await db.execute(
                    select(AgentActivityLog.id).where(
                        AgentActivityLog.id == effect_id
                    )
                )
                if receipt_result.scalar_one_or_none() is not None:
                    return

                assert answer is not None
                if mode == "schedule":
                    instruction = initial_input.get("schedule_instruction")
                    safe_instruction = (
                        instruction.strip()
                        if isinstance(instruction, str) and instruction.strip()
                        else stored_run.goal
                    )
                    action_type = "schedule_run"
                    summary = f"定时任务执行: {safe_instruction[:60]}"
                    detail = {
                        "schedule_id": str(related_id),
                        "instruction": safe_instruction,
                        "reply": answer[:500],
                    }
                else:
                    action_type = "heartbeat"
                    summary = f"Heartbeat: {answer[:80]}"
                    detail = {"reply": answer[:500]}
                    related_id = run.run_id
                db.add(
                    AgentActivityLog(
                        id=effect_id,
                        agent_id=agent_id,
                        action_type=action_type,
                        summary=summary,
                        detail_json=detail,
                        related_id=related_id,
                        created_at=self._clock(),
                    )
                )
                await db.flush()


__all__ = [
    "HeartbeatRuntimeCompletionError",
    "HeartbeatRuntimeCompletionHandler",
]
