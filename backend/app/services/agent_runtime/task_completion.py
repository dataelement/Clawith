"""Idempotent Task product updates from terminal Runtime checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Callable
import uuid

from sqlalchemy import select

from app.models.agent_run import AgentRun
from app.models.task import Task, TaskLog
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TaskRuntimeCompletionError(RuntimeError):
    """A terminal Task Run cannot be applied to its product record safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _task_log_id(run_id: uuid.UUID, checkpoint_id: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"task-terminal:{checkpoint_id}")


def _terminal_detail(checkpoint: CheckpointObservation) -> str:
    lifecycle = checkpoint.state["lifecycle"]
    status = lifecycle["status"]
    if status == "completed":
        answer = lifecycle.get("final_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise TaskRuntimeCompletionError(
                "missing_task_result",
                "completed Task checkpoint has no final answer",
            )
        return answer.strip()
    error = lifecycle.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    reason = lifecycle.get("reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else status


class TaskRuntimeCompletionHandler:
    """Set Task status and append exactly one terminal log per checkpoint."""

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
        if run.source_type != "task":
            return
        status = checkpoint.state["lifecycle"]["status"]
        if status not in _TERMINAL_STATUSES:
            return
        try:
            agent_id = uuid.UUID(run.agent_id or "")
        except ValueError as exc:
            raise TaskRuntimeCompletionError(
                "invalid_task_run_identity",
                "Task Run has no valid Agent identity",
            ) from exc

        receipt_id = _task_log_id(run.run_id, checkpoint.checkpoint_id)
        async with self._session_factory() as db:
            async with db.begin():
                run_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                        AgentRun.source_type == "task",
                    )
                )
                stored_run = run_result.scalar_one_or_none()
                if stored_run is None or stored_run.source_id is None:
                    raise TaskRuntimeCompletionError(
                        "task_source_missing",
                        "terminal Task Run has no source Task",
                    )
                try:
                    task_id = uuid.UUID(stored_run.source_id)
                except ValueError as exc:
                    raise TaskRuntimeCompletionError(
                        "invalid_task_source",
                        "terminal Task Run source_id is not a UUID",
                    ) from exc

                receipt_result = await db.execute(
                    select(TaskLog.id).where(TaskLog.id == receipt_id)
                )
                if receipt_result.scalar_one_or_none() is not None:
                    return

                task_result = await db.execute(
                    select(Task)
                    .where(
                        Task.id == task_id,
                    )
                    .with_for_update()
                )
                task = task_result.scalar_one_or_none()
                if task is None:
                    # Deleting a product Task does not delete or invalidate its
                    # authoritative execution history.
                    return
                if task.agent_id != agent_id:
                    raise TaskRuntimeCompletionError(
                        "task_agent_mismatch",
                        "terminal Runtime source Task belongs to another Agent",
                    )

                detail = _terminal_detail(checkpoint)
                is_supervision = task.type == "supervision"
                if status == "completed" and not is_supervision:
                    task.status = "done"
                    task.completed_at = self._clock()
                    content = f"✅ 任务完成\n\n{detail}"
                elif status == "completed":
                    task.status = "pending"
                    task.completed_at = None
                    content = f"✅ 督办执行完成\n\n{detail}"
                elif status == "cancelled":
                    task.status = "pending"
                    task.completed_at = None
                    label = "督办" if is_supervision else "任务"
                    content = f"⏹️ {label}执行已取消：{detail}"
                else:
                    task.status = "pending"
                    task.completed_at = None
                    label = "督办" if is_supervision else "任务"
                    content = f"❌ {label}执行失败：{detail}"
                db.add(
                    TaskLog(
                        id=receipt_id,
                        task_id=task.id,
                        content=content,
                    )
                )
                await db.flush()


__all__ = [
    "TaskRuntimeCompletionError",
    "TaskRuntimeCompletionHandler",
]
