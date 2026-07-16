"""Idempotent product updates derived from an already-committed checkpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol, cast
import uuid

from loguru import logger
from sqlalchemy import select

from app.models.agent_run import AgentRun
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
from app.services.group_realtime import publish_stored_group_message


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
    return DeliveryRequest(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        kind="terminal",
        content=_terminal_content(checkpoint, status=status),
        checkpoint_id=checkpoint.checkpoint_id,
        lifecycle_status=cast(DeliveryLifecycleStatus, status),
        group_handoff_intent=_terminal_group_handoff(checkpoint),
    )


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

        errors: list[Exception] = []
        delivery = delivery_from_checkpoint(run, product_checkpoint)
        if delivery is not None:
            receipt: DeliveryReceipt | None = None
            try:
                async with self._session_factory() as db:
                    async with db.begin():
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
            if (
                receipt is not None
                and receipt.status == "delivered"
                and isinstance(receipt.actual_session_id, uuid.UUID)
                and isinstance(receipt.message_id, uuid.UUID)
            ):
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
