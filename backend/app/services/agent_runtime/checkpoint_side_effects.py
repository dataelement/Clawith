"""Idempotent product updates derived from an already-committed checkpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

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
    DeliveryRequest,
    deliver_runtime_message,
)
from app.services.agent_runtime.projector import RuntimeProjector


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


def _validate_scope(
    run: RuntimeRunRecord,
    command: RuntimeCommandRecord,
    checkpoint: CheckpointObservation,
) -> str:
    if command.tenant_id != run.tenant_id or command.run_id != run.run_id:
        raise RuntimeCheckpointSideEffectError(
            "command_scope_mismatch",
            "post-checkpoint command does not belong to the Run",
        )
    if checkpoint.state["registry"] != run.registry:
        raise RuntimeCheckpointSideEffectError(
            "checkpoint_identity_mismatch",
            "post-checkpoint state does not match the Run Registry",
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


def delivery_from_checkpoint(
    run: RuntimeRunRecord,
    checkpoint: CheckpointObservation,
) -> DeliveryRequest | None:
    """Derive a user-visible request without consulting a product projection."""
    status = checkpoint.state["lifecycle"]["status"]
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
    )


class RuntimeCheckpointSideEffects:
    """Project and deliver one observed checkpoint using short transactions."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        projector: RuntimeProjector,
        terminal_handlers: Sequence[RuntimeTerminalProductHandler] = (),
    ) -> None:
        self._session_factory = session_factory
        self._projector = projector
        self._terminal_handlers = tuple(terminal_handlers)

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        checkpoint_id = _validate_scope(run, command, checkpoint)
        authoritative_status = checkpoint.state["lifecycle"]["status"]

        async with self._session_factory() as db:
            async with db.begin():
                projection = await self._projector.project_run(
                    db,
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                )
                if projection.authoritative_status != authoritative_status:
                    raise RuntimeCheckpointSideEffectError(
                        "projection_checkpoint_mismatch",
                        "projector latest status differs from the observed checkpoint",
                    )
                if (
                    projection.applied_checkpoint_ids
                    and checkpoint_id not in projection.applied_checkpoint_ids
                ):
                    raise RuntimeCheckpointSideEffectError(
                        "projection_checkpoint_mismatch",
                        "projector did not apply the observed checkpoint",
                    )

        errors: list[Exception] = []
        delivery = delivery_from_checkpoint(run, checkpoint)
        if delivery is not None:
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
                            await deliver_runtime_message(db, delivery)
            except Exception as exc:
                errors.append(exc)

        if authoritative_status in _TERMINAL_STATUSES:
            for terminal_handler in self._terminal_handlers:
                try:
                    await terminal_handler.handle(
                        run=run,
                        checkpoint=checkpoint,
                    )
                except Exception as exc:
                    errors.append(exc)

        if errors:
            raise errors[0]


__all__ = [
    "RuntimeCheckpointSideEffectError",
    "RuntimeCheckpointSideEffects",
    "delivery_from_checkpoint",
]
