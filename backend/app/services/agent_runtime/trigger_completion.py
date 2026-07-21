"""Idempotent TriggerExecution updates from terminal Runtime checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Callable
import uuid

from sqlalchemy import select

from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.trigger_execution import TriggerExecution
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TriggerRuntimeCompletionError(RuntimeError):
    """A terminal Trigger Run cannot be applied to its product record safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _receipt_id(run_id: uuid.UUID, checkpoint_id: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"trigger-terminal:{checkpoint_id}")


def _terminal_detail(checkpoint: CheckpointObservation) -> str:
    lifecycle = checkpoint.state["lifecycle"]
    status = lifecycle["status"]
    if status == "completed":
        answer = lifecycle.get("final_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise TriggerRuntimeCompletionError(
                "missing_trigger_result",
                "completed Trigger checkpoint has no final answer",
            )
        return answer.strip()
    error = lifecycle.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    reason = lifecycle.get("reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else status


def _reflection_content(*, status: str, detail: str) -> str:
    if status == "completed":
        return detail
    if status == "cancelled":
        return f"⏹️ 触发器执行已取消：{detail}"
    return f"❌ 触发器执行失败：{detail}"


class TriggerRuntimeCompletionHandler:
    """Set execution status and append one terminal reflection message."""

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
        if run.source_type != "trigger":
            return
        status = checkpoint.state["lifecycle"]["status"]
        if status not in _TERMINAL_STATUSES:
            return
        detail = _terminal_detail(checkpoint)
        receipt_id = _receipt_id(run.run_id, checkpoint.checkpoint_id)

        async with self._session_factory() as db:
            async with db.begin():
                run_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                        AgentRun.source_type == "trigger",
                    )
                )
                stored_run = run_result.scalar_one_or_none()
                if (
                    stored_run is None
                    or stored_run.source_execution_id is None
                    or stored_run.source_id is None
                    or stored_run.session_id is None
                    or stored_run.agent_id is None
                ):
                    raise TriggerRuntimeCompletionError(
                        "trigger_source_missing",
                        "terminal Trigger Run has incomplete source identity",
                    )
                try:
                    execution_id = uuid.UUID(stored_run.source_execution_id)
                    trigger_id = uuid.UUID(stored_run.source_id)
                except ValueError as exc:
                    raise TriggerRuntimeCompletionError(
                        "invalid_trigger_source",
                        "terminal Trigger Run source identity is not a UUID",
                    ) from exc

                receipt_result = await db.execute(
                    select(ChatMessage.id).where(ChatMessage.id == receipt_id)
                )
                if receipt_result.scalar_one_or_none() is not None:
                    return

                execution_result = await db.execute(
                    select(TriggerExecution)
                    .where(TriggerExecution.id == execution_id)
                    .with_for_update()
                )
                execution = execution_result.scalar_one_or_none()
                if execution is None:
                    # Trigger deletion may cascade the product occurrence while
                    # its authoritative Runtime history remains queryable.
                    return
                if (
                    execution.trigger_id != trigger_id
                    or execution.agent_id != stored_run.agent_id
                ):
                    raise TriggerRuntimeCompletionError(
                        "trigger_execution_scope_mismatch",
                        "terminal Runtime source does not match TriggerExecution scope",
                    )

                session_result = await db.execute(
                    select(ChatSession).where(
                        ChatSession.tenant_id == run.tenant_id,
                        ChatSession.id == stored_run.session_id,
                        ChatSession.session_type == "trigger",
                        ChatSession.agent_id == stored_run.agent_id,
                    )
                )
                session = session_result.scalar_one_or_none()
                if session is None:
                    raise TriggerRuntimeCompletionError(
                        "trigger_session_missing",
                        "terminal Trigger Run reflection session is unavailable",
                    )

                now = self._clock()
                execution.status = "completed" if status == "completed" else "failed"
                execution.finished_at = now
                execution.lease_owner = None
                execution.lease_expires_at = None
                execution.last_error = None if status == "completed" else detail
                db.add(
                    ChatMessage(
                        id=receipt_id,
                        agent_id=stored_run.agent_id,
                        user_id=session.user_id,
                        role="assistant",
                        content=_reflection_content(status=status, detail=detail),
                        conversation_id=str(session.id),
                        participant_id=session.participant_id,
                        mentions=[],
                        created_at=now,
                    )
                )
                session.last_message_at = now
                await db.flush()


__all__ = [
    "TriggerRuntimeCompletionError",
    "TriggerRuntimeCompletionHandler",
]
