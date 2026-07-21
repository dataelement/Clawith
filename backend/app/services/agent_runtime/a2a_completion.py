"""Idempotent A2A message projection and source-Run callback."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Callable
import uuid

from sqlalchemy import select

from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.gateway_message import GatewayMessage
from app.services.agent_runtime.a2a_runtime import a2a_mode_from_correlation
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.contracts import ResumeRunCommand
from app.services.participant_identity import get_or_create_agent_participant


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class A2ARuntimeCompletionError(RuntimeError):
    """A terminal target Run cannot be delivered or correlated safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _message_id(run_id: uuid.UUID, checkpoint_id: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"a2a-terminal:{checkpoint_id}")


def _resume_idempotency_key(run_id: uuid.UUID, checkpoint_id: str) -> str:
    occurrence_id = uuid.uuid5(run_id, f"a2a-resume:{checkpoint_id}")
    return f"a2a-result:{occurrence_id}"


def _gateway_reply_id(run_id: uuid.UUID, checkpoint_id: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"gateway-a2a-terminal:{checkpoint_id}")


def _terminal_result(checkpoint: CheckpointObservation) -> tuple[str, dict]:
    lifecycle = checkpoint.state["lifecycle"]
    status = lifecycle["status"]
    if status == "completed":
        answer = lifecycle.get("final_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise A2ARuntimeCompletionError(
                "a2a_result_missing",
                "completed A2A target checkpoint has no final answer",
            )
        result_summary = lifecycle.get("result_summary")
        artifact_refs: list = []
        if isinstance(result_summary, Mapping):
            raw_refs = result_summary.get("artifact_refs")
            if isinstance(raw_refs, list):
                artifact_refs = list(raw_refs)
        answer = answer.strip()
        return answer, {
            "status": "completed",
            "result_summary": answer,
            "artifact_refs": artifact_refs,
            "error": None,
        }

    error = lifecycle.get("error")
    error_payload = dict(error) if isinstance(error, Mapping) else {}
    reason = lifecycle.get("reason")
    if isinstance(reason, str) and reason.strip():
        error_payload.setdefault("code", reason.strip())
    error_code = error_payload.get("code")
    if not isinstance(error_code, str) or not error_code.strip():
        error_code = f"a2a_target_{status}"
        error_payload["code"] = error_code
    if status == "cancelled":
        content = f"⏹️ Agent collaboration was cancelled: {error_code}"
    else:
        content = f"❌ Agent collaboration failed: {error_code}"
    return content, {
        "status": status,
        "result_summary": None,
        "artifact_refs": [],
        "error": error_payload,
    }


class A2ARuntimeCompletionHandler:
    """Append the target conclusion and resume response-bearing source Runs."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _handle_gateway_result(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
        gateway_message_id: uuid.UUID,
        source_agent_id: uuid.UUID,
        content: str,
    ) -> None:
        receipt_id = _message_id(run.run_id, checkpoint.checkpoint_id)
        reply_id = _gateway_reply_id(run.run_id, checkpoint.checkpoint_id)
        async with self._session_factory() as db:
            async with db.begin():
                target_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                        AgentRun.source_type == "a2a",
                        AgentRun.run_kind == "delegated",
                    )
                )
                target_run = target_result.scalar_one_or_none()
                if (
                    target_run is None
                    or target_run.agent_id is None
                    or target_run.origin_agent_id != source_agent_id
                    or target_run.session_id is None
                    or target_run.source_id != str(target_run.session_id)
                ):
                    raise A2ARuntimeCompletionError(
                        "gateway_a2a_target_identity_missing",
                        "gateway A2A target Run has incomplete linkage",
                    )

                inbound = await db.get(GatewayMessage, gateway_message_id)
                if (
                    inbound is None
                    or inbound.agent_id != target_run.agent_id
                    or inbound.sender_agent_id != source_agent_id
                    or inbound.conversation_id != str(target_run.session_id)
                ):
                    raise A2ARuntimeCompletionError(
                        "gateway_a2a_message_mismatch",
                        "gateway A2A source message does not match the target Run",
                    )

                existing_reply = await db.get(GatewayMessage, reply_id)
                if existing_reply is not None:
                    if (
                        existing_reply.agent_id != source_agent_id
                        or existing_reply.sender_agent_id != target_run.agent_id
                        or existing_reply.content != content
                        or existing_reply.conversation_id != str(target_run.session_id)
                    ):
                        raise A2ARuntimeCompletionError(
                            "gateway_a2a_reply_mismatch",
                            "gateway A2A reply receipt has different immutable output",
                        )
                    return

                agent_result = await db.execute(
                    select(Agent).where(
                        Agent.tenant_id == run.tenant_id,
                        Agent.id == target_run.agent_id,
                    )
                )
                target_agent = agent_result.scalar_one_or_none()
                if target_agent is None:
                    raise A2ARuntimeCompletionError(
                        "a2a_target_agent_missing",
                        "gateway A2A target Agent is unavailable",
                    )
                session_result = await db.execute(
                    select(ChatSession).where(
                        ChatSession.tenant_id == run.tenant_id,
                        ChatSession.id == target_run.session_id,
                        ChatSession.session_type == "a2a",
                    )
                )
                session = session_result.scalar_one_or_none()
                if session is None or target_agent.id not in {
                    session.agent_id,
                    session.peer_agent_id,
                }:
                    raise A2ARuntimeCompletionError(
                        "a2a_session_scope_mismatch",
                        "gateway A2A session does not contain the target Agent",
                    )
                participant = await get_or_create_agent_participant(
                    db,
                    target_agent.id,
                    target_agent.name,
                    target_agent.avatar_url,
                )

                now = self._clock()
                receipt_result = await db.execute(
                    select(ChatMessage.id).where(ChatMessage.id == receipt_id)
                )
                if receipt_result.scalar_one_or_none() is None:
                    db.add(
                        ChatMessage(
                            id=receipt_id,
                            agent_id=session.agent_id,
                            user_id=target_run.origin_user_id,
                            role="assistant",
                            content=content,
                            conversation_id=str(session.id),
                            participant_id=participant.id,
                            mentions=[],
                            created_at=now,
                        )
                    )
                inbound.status = "completed"
                inbound.result = content
                inbound.completed_at = now
                db.add(
                    GatewayMessage(
                        id=reply_id,
                        agent_id=source_agent_id,
                        sender_agent_id=target_agent.id,
                        content=content,
                        status="pending",
                        conversation_id=str(session.id),
                    )
                )
                session.last_message_at = now
                await db.flush()

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        if run.source_type != "a2a":
            return
        status = checkpoint.state["lifecycle"]["status"]
        if status not in _TERMINAL_STATUSES:
            return
        content, result_payload = _terminal_result(checkpoint)
        initial_input = checkpoint.state["snapshots"].initial_input
        raw_gateway_message_id = initial_input.get("gateway_message_id")
        if raw_gateway_message_id is not None:
            try:
                gateway_message_id = uuid.UUID(str(raw_gateway_message_id))
                source_agent_id = uuid.UUID(
                    str(initial_input.get("gateway_reply_agent_id", ""))
                )
            except ValueError as exc:
                raise A2ARuntimeCompletionError(
                    "gateway_a2a_identity_invalid",
                    "gateway A2A checkpoint has invalid reply metadata",
                ) from exc
            await self._handle_gateway_result(
                run=run,
                checkpoint=checkpoint,
                gateway_message_id=gateway_message_id,
                source_agent_id=source_agent_id,
                content=content,
            )
            return
        receipt_id = _message_id(run.run_id, checkpoint.checkpoint_id)

        async with self._session_factory() as db:
            async with db.begin():
                target_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                        AgentRun.source_type == "a2a",
                        AgentRun.run_kind == "delegated",
                    )
                )
                target_run = target_result.scalar_one_or_none()
                if (
                    target_run is None
                    or target_run.agent_id is None
                    or target_run.origin_agent_id is None
                    or target_run.parent_run_id is None
                    or target_run.session_id is None
                    or target_run.source_id is None
                    or target_run.correlation_id is None
                ):
                    raise A2ARuntimeCompletionError(
                        "a2a_target_identity_missing",
                        "terminal A2A target Run has incomplete linkage",
                    )
                try:
                    source_session_id = uuid.UUID(target_run.source_id)
                except ValueError as exc:
                    raise A2ARuntimeCompletionError(
                        "a2a_session_identity_invalid",
                        "terminal A2A target source_id is not a session UUID",
                    ) from exc
                if source_session_id != target_run.session_id:
                    raise A2ARuntimeCompletionError(
                        "a2a_session_identity_mismatch",
                        "terminal A2A target session does not match source_id",
                    )
                try:
                    mode = a2a_mode_from_correlation(target_run.correlation_id)
                except RuntimeError as exc:
                    raise A2ARuntimeCompletionError(
                        "a2a_correlation_invalid",
                        str(exc),
                    ) from exc

                receipt_result = await db.execute(
                    select(ChatMessage.id).where(ChatMessage.id == receipt_id)
                )
                if receipt_result.scalar_one_or_none() is not None:
                    return

                source_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == target_run.parent_run_id,
                    )
                )
                source_run = source_result.scalar_one_or_none()
                if (
                    source_run is None
                    or source_run.agent_id != target_run.origin_agent_id
                    or source_run.runtime_type != "langgraph"
                ):
                    raise A2ARuntimeCompletionError(
                        "a2a_source_identity_mismatch",
                        "A2A source Run does not match the target parent linkage",
                    )

                agent_result = await db.execute(
                    select(Agent).where(
                        Agent.tenant_id == run.tenant_id,
                        Agent.id == target_run.agent_id,
                    )
                )
                target_agent = agent_result.scalar_one_or_none()
                if target_agent is None:
                    raise A2ARuntimeCompletionError(
                        "a2a_target_agent_missing",
                        "A2A target Agent is unavailable for message projection",
                    )
                participant = await get_or_create_agent_participant(
                    db,
                    target_agent.id,
                    target_agent.name,
                    target_agent.avatar_url,
                )

                session_result = await db.execute(
                    select(ChatSession).where(
                        ChatSession.tenant_id == run.tenant_id,
                        ChatSession.id == target_run.session_id,
                        ChatSession.session_type == "a2a",
                    )
                )
                session = session_result.scalar_one_or_none()
                if session is None or target_agent.id not in {
                    session.agent_id,
                    session.peer_agent_id,
                }:
                    raise A2ARuntimeCompletionError(
                        "a2a_session_scope_mismatch",
                        "A2A target session does not contain the target Agent",
                    )

                now = self._clock()
                db.add(
                    ChatMessage(
                        id=receipt_id,
                        agent_id=session.agent_id,
                        user_id=target_run.origin_user_id,
                        role="assistant",
                        content=content,
                        conversation_id=str(session.id),
                        participant_id=participant.id,
                        mentions=[],
                        created_at=now,
                    )
                )
                session.last_message_at = now

                if mode in {"consult", "task_delegate"}:
                    await RuntimeCommandIntake(db).resume_run(
                        ResumeRunCommand(
                            tenant_id=run.tenant_id,
                            run_id=source_run.id,
                            idempotency_key=_resume_idempotency_key(
                                run.run_id,
                                checkpoint.checkpoint_id,
                            ),
                            payload={
                                "resume_type": "agent_result",
                                "correlation_id": target_run.correlation_id,
                                "payload": {
                                    "target_run_id": str(target_run.id),
                                    "target_agent_id": str(target_agent.id),
                                    **result_payload,
                                },
                            },
                            actor_user_id=target_run.origin_user_id,
                            actor_agent_id=target_agent.id,
                        )
                    )
                await db.flush()


__all__ = [
    "A2ARuntimeCompletionError",
    "A2ARuntimeCompletionHandler",
]
