"""Idempotent product updates derived from an already-committed checkpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from typing import Protocol, cast
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.delivery import (
    DeliveryLifecycleStatus,
    DeliveryReceipt,
    DeliveryRequest,
    deliver_runtime_message,
)
from app.services.agent_runtime.state import runtime_messages_as_json
from app.services.agent_runtime.tool_execution import sanitize_tool_arguments
from app.services.builtin_tool_definitions import builtin_sensitive_paths
from app.services.group_realtime import publish_stored_group_message
from app.services.experience_retrieval import record_experience_citations


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_WAITING_PROMPT = "需要你的确认或补充信息后才能继续。"


class RuntimeCheckpointSideEffectError(RuntimeError):
    """A committed checkpoint cannot be projected or delivered safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeTerminalProductHandler(Protocol):
    """Apply one source-specific product result without driving the Graph."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None: ...


class RuntimeCheckpointProductHandler(Protocol):
    """Apply source-specific work for any committed checkpoint status."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None: ...


def _validate_scope(
    run: RuntimeRunRecord,
    command: RuntimeCommandRecord,
    checkpoint: CheckpointObservation | None,
) -> str | None:
    if command.tenant_id != run.tenant_id or command.run_id != run.run_id:
        raise RuntimeCheckpointSideEffectError(
            "command_scope_mismatch",
            "post-checkpoint command does not belong to the Run",
        )
    if checkpoint is None:
        if command.command_type != "cancel":
            raise RuntimeCheckpointSideEffectError(
                "missing_checkpoint",
                "only cancel-before-start may synchronize without a checkpoint",
            )
        return None
    if checkpoint.metadata.get("clawith_run_id") != str(run.run_id):
        raise RuntimeCheckpointSideEffectError(
            "checkpoint_identity_mismatch",
            "post-checkpoint metadata does not match the Run Registry",
        )
    checkpoint_id = checkpoint.checkpoint_id.strip()
    if not checkpoint_id:
        raise RuntimeCheckpointSideEffectError(
            "invalid_checkpoint_id",
            "post-checkpoint side effects require a checkpoint ID",
        )
    return checkpoint_id


def _text_field(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _waiting_delivery(
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
) -> DeliveryRequest:
    waiting = checkpoint.state["lifecycle"].get("waiting_request")
    if not isinstance(waiting, Mapping):
        raise RuntimeCheckpointSideEffectError(
            "invalid_waiting_request",
            "waiting_user checkpoint requires a waiting request",
        )
    interrupt_id = _text_field(waiting.get("correlation_id"))
    if interrupt_id is None:
        raise RuntimeCheckpointSideEffectError(
            "invalid_waiting_request",
            "waiting_user checkpoint requires a correlation ID",
        )
    content = next(
        (
            text
            for field in ("question", "prompt", "reason")
            if (text := _text_field(waiting.get(field))) is not None
        ),
        _WAITING_PROMPT,
    )
    return DeliveryRequest(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        kind="waiting",
        content=content,
        checkpoint_id=checkpoint.checkpoint_id,
        lifecycle_status="waiting_user",
        interrupt_id=interrupt_id,
    )


def _terminal_content(checkpoint: CheckpointObservation, *, status: str) -> str:
    lifecycle = checkpoint.state["lifecycle"]
    raw_request = lifecycle.get("delivery_request")
    if raw_request is not None and not isinstance(raw_request, Mapping):
        raise RuntimeCheckpointSideEffectError(
            "invalid_delivery_request",
            "checkpoint delivery_request must be an object",
        )
    requested = _text_field(raw_request.get("content")) if isinstance(raw_request, Mapping) else None
    if requested is not None:
        return requested
    final_answer = _text_field(lifecycle.get("final_answer"))
    if status == "completed" and final_answer is None:
        raise RuntimeCheckpointSideEffectError(
            "missing_terminal_content",
            "completed checkpoint has no user-visible answer",
        )
    return final_answer or ""


def _failure_metadata(checkpoint: CheckpointObservation) -> tuple[str | None, str | None]:
    lifecycle = checkpoint.state["lifecycle"]
    error = lifecycle.get("error")
    if not isinstance(error, Mapping):
        return None, None
    return _text_field(error.get("code")), _text_field(error.get("message"))


def _terminal_group_handoff(
    checkpoint: CheckpointObservation,
) -> dict | None:
    raw_request = checkpoint.state["lifecycle"].get("delivery_request")
    if not isinstance(raw_request, Mapping):
        return None
    raw_handoff = raw_request.get("group_handoff")
    if raw_handoff is None:
        return None
    if not isinstance(raw_handoff, Mapping):
        raise RuntimeCheckpointSideEffectError(
            "invalid_delivery_request",
            "checkpoint group_handoff intent must be an object",
        )
    return dict(raw_handoff)


def _terminal_thinking(
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
) -> str | None:
    for message in reversed(runtime_messages_as_json(checkpoint.state)):
        if (
            message.get("role") == "assistant"
            and message.get("runtime_run_id") == str(run.run_id)
            and message.get("runtime_intent") == "finish"
        ):
            return _text_field(message.get("reasoning_content"))
    return None


def delivery_from_checkpoint(
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
) -> DeliveryRequest | None:
    """Derive a user-visible request without consulting a product projection."""
    status = checkpoint.state["lifecycle"]["status"]
    if run.system_role == "group_planning" and status == "completed":
        return None
    if status == "waiting_user":
        return _waiting_delivery(run, checkpoint)
    if status not in _TERMINAL_STATUSES:
        return None
    failure_code, failure_message = (
        _failure_metadata(checkpoint) if status == "failed" else (None, None)
    )
    return DeliveryRequest(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        kind="terminal",
        content=_terminal_content(checkpoint, status=status),
        checkpoint_id=checkpoint.checkpoint_id,
        lifecycle_status=cast(DeliveryLifecycleStatus, status),
        group_handoff_intent=_terminal_group_handoff(checkpoint),
        failure_code=failure_code,
        failure_message=failure_message,
        thinking=_terminal_thinking(run, checkpoint),
    )


def _event_payload(checkpoint: CheckpointObservation) -> dict:
    lifecycle = checkpoint.state["lifecycle"]
    status = lifecycle["status"]
    payload: dict = {"status": status}
    if status.startswith("waiting_"):
        waiting = lifecycle.get("waiting_request")
        if isinstance(waiting, Mapping):
            payload.update(dict(waiting))
            payload.setdefault("waiting_type", status.removeprefix("waiting_"))
    else:
        reason = _text_field(lifecycle.get("reason"))
        if reason is not None:
            payload["reason"] = reason
        error = lifecycle.get("error")
        if isinstance(error, Mapping):
            error_code = _text_field(error.get("code"))
            if error_code is not None:
                payload["error_code"] = error_code
    return payload


def _tool_arguments(call: Mapping[str, object], tool_name: str) -> dict:
    function = call.get("function")
    raw = function.get("arguments") if isinstance(function, Mapping) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {"raw": raw}
    if not isinstance(raw, dict):
        raw = {}
    return sanitize_tool_arguments(
        raw,
        sensitive_paths=builtin_sensitive_paths(tool_name),
    )


def _runtime_observation_events(
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
) -> tuple[list[tuple[str, str, dict, str, str | None]], dict[str, dict]]:
    """Derive replayable Web Chat activity from stable Runtime messages.

    These remain ``status_changed`` product events so the durable event schema
    stays backward compatible. ``activity_type`` is the Web Chat projection
    discriminator; idempotency keys are based on stable message/tool-call IDs.
    """
    events: list[tuple[str, str, dict, str, str | None]] = []
    calls: dict[str, dict] = {}
    messages = runtime_messages_as_json(checkpoint.state)

    for message in messages:
        if message.get("role") != "assistant" or message.get("runtime_run_id") != str(run.run_id):
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        reasoning = _text_field(message.get("reasoning_content"))
        if reasoning is not None:
            events.append(
                (
                    "status_changed",
                    "Runtime model reasoning available",
                    {
                        "status": "running",
                        "activity_type": "thinking",
                        "content": reasoning,
                        "message_id": message_id,
                    },
                    f"activity:thinking:{message_id}",
                    None,
                )
            )
        content = _text_field(message.get("content"))
        runtime_intent = message.get("runtime_intent")
        if content is not None and runtime_intent not in {"finish", "wait"}:
            events.append(
                (
                    "status_changed",
                    "Runtime model progress available",
                    {
                        "status": "running",
                        "activity_type": "assistant_progress",
                        "content": content,
                        "message_id": message_id,
                    },
                    f"activity:progress:{message_id}",
                    None,
                )
            )
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                continue
            call_id = _text_field(raw_call.get("id"))
            function = raw_call.get("function")
            tool_name = _text_field(function.get("name")) if isinstance(function, Mapping) else None
            if call_id is None or tool_name is None:
                continue
            detail = {
                "call_id": call_id,
                "name": tool_name,
                "args": _tool_arguments(raw_call, tool_name),
                "reasoning_content": reasoning or "",
                "assistant_message_id": message_id,
            }
            calls[call_id] = detail
            events.append(
                (
                    "status_changed",
                    f"Runtime tool {tool_name} started",
                    {
                        "status": "running",
                        "activity_type": "tool_call",
                        **detail,
                    },
                    f"activity:tool:{call_id}:running",
                    None,
                )
            )

    for message in messages:
        if message.get("role") not in {"tool", "tool_result"}:
            continue
        call_id = _text_field(message.get("tool_call_id") or message.get("call_id"))
        if call_id is None or call_id not in calls:
            continue
        execution_status = _text_field(message.get("execution_status")) or "succeeded"
        ui_status = "running" if execution_status == "pending" else "done"
        result = str(message.get("content") or "")
        error_code = _text_field(message.get("error_code"))
        payload = {
            "status": ui_status,
            "activity_type": "tool_call",
            **calls[call_id],
            "result": result,
            "execution_status": execution_status,
        }
        if error_code is not None:
            payload["error_code"] = error_code
        events.append(
            (
                "status_changed",
                f"Runtime tool {calls[call_id]['name']} {execution_status}",
                payload,
                f"activity:tool:{call_id}:{execution_status}",
                None,
            )
        )
    return events, calls


async def _record_direct_tool_history(
    db,
    *,
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
    calls: Mapping[str, dict],
) -> None:
    """Persist settled direct-chat tools for refresh and reconnect recovery."""
    if run.session_id is None or run.agent_id is None or not calls:
        return
    run_result = await db.execute(
        select(AgentRun).where(
            AgentRun.tenant_id == run.tenant_id,
            AgentRun.id == run.run_id,
        )
    )
    stored = run_result.scalar_one_or_none()
    if (
        stored is None
        or stored.origin_user_id is None
        or not isinstance(stored.delivery_target, dict)
        or stored.delivery_target.get("kind") != "direct"
    ):
        return
    created_at = checkpoint.created_at or datetime.now(UTC)
    for message in runtime_messages_as_json(checkpoint.state):
        if message.get("role") not in {"tool", "tool_result"}:
            continue
        call_id = _text_field(message.get("tool_call_id") or message.get("call_id"))
        if call_id is None or call_id not in calls:
            continue
        execution_status = _text_field(message.get("execution_status")) or "succeeded"
        if execution_status == "pending":
            continue
        detail = calls[call_id]
        content = json.dumps(
            {
                "name": detail["name"],
                "args": detail["args"],
                "status": "done",
                "execution_status": execution_status,
                "result": str(message.get("content") or ""),
                "tool_call_id": call_id,
                "reasoning_content": detail.get("reasoning_content") or "",
                **(
                    {"error_code": message["error_code"]}
                    if isinstance(message.get("error_code"), str)
                    else {}
                ),
            },
            ensure_ascii=False,
            default=str,
        )
        await db.execute(
            insert(ChatMessage)
            .values(
                id=uuid.uuid5(run.run_id, f"chat-tool:{call_id}"),
                agent_id=uuid.UUID(run.agent_id),
                user_id=stored.origin_user_id,
                role="tool_call",
                content=content,
                conversation_id=run.session_id,
                participant_id=None,
                mentions=[],
                created_at=created_at,
            )
            .on_conflict_do_nothing()
        )


async def _record_lifecycle_events(
    db,
    *,
    run: RuntimeRunRecord,
    command: RuntimeCommandRecord,
    checkpoint: CheckpointObservation | None,
) -> None:
    """Project committed Graph/control boundaries into an idempotent event log."""
    now = datetime.now(UTC)
    agent_id = uuid.UUID(run.agent_id) if run.agent_id is not None else None
    events: list[tuple[str, str, dict, str, str | None]] = []
    if checkpoint is None:
        events.append(
            (
                "run_cancelled",
                "Runtime Run cancelled before start",
                {"status": "cancelled", "reason": "cancelled_before_start"},
                f"command:{command.id}:run_cancelled",
                None,
            )
        )
    else:
        observation_events, calls = _runtime_observation_events(run, checkpoint)
        events.extend(observation_events)
        await _record_direct_tool_history(
            db,
            run=run,
            checkpoint=checkpoint,
            calls=calls,
        )
        if command.command_type == "resume":
            events.append(
                (
                    "resumed",
                    "Runtime Run resumed",
                    {"status": "running"},
                    f"command:{command.id}:resumed",
                    checkpoint.checkpoint_id,
                )
            )
        status = checkpoint.state["lifecycle"]["status"]
        event_type = {
            "waiting_user": "waiting_started",
            "waiting_external": "waiting_started",
            "waiting_agent": "waiting_started",
            "completed": "run_completed",
            "failed": "run_failed",
            "cancelled": "run_cancelled",
        }.get(status)
        if event_type is not None:
            events.append(
                (
                    event_type,
                    f"Runtime Run {status.replace('_', ' ')}",
                    _event_payload(checkpoint),
                    f"checkpoint:{checkpoint.checkpoint_id}:{event_type}",
                    checkpoint.checkpoint_id,
                )
            )

    for position, (event_type, summary, payload, key, checkpoint_id) in enumerate(events):
        statement = (
            insert(AgentRunEvent)
            .values(
                id=uuid.uuid5(run.run_id, f"lifecycle-event:{key}"),
                tenant_id=run.tenant_id,
                run_id=run.run_id,
                agent_id=agent_id,
                event_type=event_type,
                summary=summary,
                payload=payload,
                artifact_refs=[],
                idempotency_key=key,
                source_checkpoint_id=checkpoint_id,
                created_at=now + timedelta(microseconds=position),
            )
            .on_conflict_do_nothing()
        )
        await db.execute(statement)


class RuntimeCheckpointSideEffects:
    """Synchronize products after an already-settled Graph/control boundary."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        checkpoint_handlers: Sequence[RuntimeCheckpointProductHandler] = (),
        terminal_handlers: Sequence[RuntimeTerminalProductHandler] = (),
    ) -> None:
        self._session_factory = session_factory
        self._checkpoint_handlers = tuple(checkpoint_handlers)
        self._terminal_handlers = tuple(terminal_handlers)

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        _validate_scope(run, command, checkpoint)
        if checkpoint is None:
            async with self._session_factory() as db:
                async with db.begin():
                    result = await db.execute(
                        select(AgentRun).where(
                            AgentRun.tenant_id == run.tenant_id,
                            AgentRun.id == run.run_id,
                        )
                    )
                    stored = result.scalar_one_or_none()
                    if stored is None:
                        raise RuntimeCheckpointSideEffectError(
                            "run_not_found",
                            "cancelled Run does not exist",
                        )
                    stored.lane_held = False
                    stored.lane_claimed_at = None
                    await _record_lifecycle_events(
                        db,
                        run=run,
                        command=command,
                        checkpoint=None,
                    )
                    await db.flush()
            return

        product_checkpoint = checkpoint
        if command.command_type == "cancel":
            lifecycle = {
                **checkpoint.state["lifecycle"],
                "status": "cancelled",
                "next_route": "terminal",
                "reason": command.payload.get("reason") or "cancelled_by_command",
                "waiting_request": None,
            }
            product_checkpoint = replace(
                checkpoint,
                state={**checkpoint.state, "lifecycle": lifecycle},
                next_nodes=(),
                tasks=(),
                interrupts=(),
            )
        authoritative_status = product_checkpoint.state["lifecycle"]["status"]
        if authoritative_status == "failed":
            lifecycle = product_checkpoint.state["lifecycle"]
            error = lifecycle.get("error")
            error_code = _text_field(error.get("code")) if isinstance(error, Mapping) else None
            error_message = (
                _text_field(error.get("message")) if isinstance(error, Mapping) else None
            )
            logger.error(
                "[RuntimeFailure] run_id={} agent_id={} command_id={} checkpoint_id={} "
                "reason={} error_code={} error_message={!r}",
                run.run_id,
                run.agent_id,
                command.id,
                product_checkpoint.checkpoint_id,
                _text_field(lifecycle.get("reason")),
                error_code,
                error_message,
            )

        errors: list[Exception] = []
        delivery = delivery_from_checkpoint(run, product_checkpoint)
        receipt: DeliveryReceipt | None = None
        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await _record_lifecycle_events(
                        db,
                        run=run,
                        command=command,
                        checkpoint=product_checkpoint,
                    )
                    if delivery is not None:
                        status_result = await db.execute(
                            select(AgentRun.delivery_status).where(
                                AgentRun.tenant_id == run.tenant_id,
                                AgentRun.id == run.run_id,
                            )
                        )
                        delivery_status = status_result.scalar_one_or_none()
                        if delivery_status is None:
                            raise RuntimeCheckpointSideEffectError(
                                "run_not_found",
                                "post-checkpoint delivery Run does not exist",
                            )
                        if delivery_status != "not_required":
                            receipt = await deliver_runtime_message(db, delivery)
        except Exception as exc:
            errors.append(exc)
        if delivery is not None:
            if (
                receipt is not None
                and receipt.status == "delivered"
                and isinstance(receipt.actual_session_id, uuid.UUID)
                and isinstance(receipt.message_id, uuid.UUID)
            ):
                if (
                    delivery.kind == "terminal"
                    and delivery.lifecycle_status == "completed"
                ):
                    try:
                        await record_experience_citations(
                            delivery.content,
                            agent_id=run.agent_id,
                            session_id=receipt.actual_session_id,
                            message_id=receipt.message_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            f"[Experience] Citation telemetry failed after delivery commit: {exc}"
                        )
                try:
                    await publish_stored_group_message(
                        self._session_factory,
                        tenant_id=run.tenant_id,
                        session_id=receipt.actual_session_id,
                        message_id=receipt.message_id,
                    )
                except Exception as exc:
                    logger.warning(f"[GroupRealtime] Runtime publish lookup failed: {exc}")

        for checkpoint_handler in self._checkpoint_handlers:
            try:
                await checkpoint_handler.handle(
                    run=run,
                    checkpoint=product_checkpoint,
                )
            except Exception as exc:
                errors.append(exc)

        if authoritative_status in _TERMINAL_STATUSES:
            for terminal_handler in self._terminal_handlers:
                try:
                    await terminal_handler.handle(
                        run=run,
                        checkpoint=product_checkpoint,
                    )
                except Exception as exc:
                    errors.append(exc)

        if errors:
            raise errors[0]


__all__ = [
    "RuntimeCheckpointSideEffectError",
    "RuntimeCheckpointProductHandler",
    "RuntimeCheckpointSideEffects",
    "delivery_from_checkpoint",
]
