"""Exactly-once Session Context merging from terminal Runtime checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import uuid

from sqlalchemy import select

from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.session_context_service import (
    SessionContextCandidate,
    SessionContextConflict,
    SessionContextDelta,
    SessionContextService,
    SessionContextSnapshot,
)
from app.services.agent_runtime.state import JsonObject


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class SessionContextCompletionError(RuntimeError):
    """A terminal delta cannot be applied without violating its receipt."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SessionCompactRequest:
    """Immutable input for one optimistic Session Compact attempt."""

    tenant_id: uuid.UUID
    session_id: uuid.UUID
    source_agent_id: uuid.UUID | None
    checkpoint_id: str
    snapshot: SessionContextSnapshot
    messages: tuple[JsonObject, ...]
    delta: SessionContextDelta | None


class SessionContextCompactor(Protocol):
    """Generate a candidate without writing product or checkpoint state."""

    async def compact(self, request: SessionCompactRequest) -> SessionContextCandidate: ...


class SessionContextCompletionHandler:
    """Merge one terminal delta and its Run receipt in the same transaction."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        compactor: SessionContextCompactor,
        context_service: SessionContextService | None = None,
        max_conflict_retries: int = 3,
    ) -> None:
        if max_conflict_retries <= 0:
            raise ValueError("max_conflict_retries must be positive")
        self._session_factory = session_factory
        self._compactor = compactor
        self._context_service = context_service or SessionContextService()
        self._max_conflict_retries = max_conflict_retries

    @staticmethod
    def _checkpoint_delta(
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> SessionContextDelta | None:
        lifecycle = checkpoint.state["lifecycle"]
        if lifecycle["status"] not in _TERMINAL_STATUSES:
            return None
        value = lifecycle.get("session_context_delta")
        if value is None:
            return None
        return SessionContextDelta.from_json(
            value,
            expected_source_run_id=run.run_id,
        )

    @staticmethod
    def _receipt_state(run: AgentRun, checkpoint_id: str) -> bool:
        receipt = run.session_context_applied_checkpoint_id
        if receipt is None:
            return False
        if receipt != checkpoint_id:
            raise SessionContextCompletionError(
                "session_context_receipt_conflict",
                "Run already records a different terminal Session Context checkpoint",
            )
        return True

    async def _load_request(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
        delta: SessionContextDelta,
    ) -> SessionCompactRequest | None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentRun).where(
                    AgentRun.tenant_id == run.tenant_id,
                    AgentRun.id == run.run_id,
                )
            )
            stored_run = result.scalar_one_or_none()
            if stored_run is None:
                raise SessionContextCompletionError(
                    "run_not_found",
                    "terminal Session Context source Run does not exist",
                )
            if self._receipt_state(stored_run, checkpoint.checkpoint_id):
                return None
            if stored_run.session_id is None:
                return None
            snapshot = await self._context_service.load_snapshot(
                db,
                tenant_id=run.tenant_id,
                session_id=stored_run.session_id,
            )
            messages = await self._context_service.load_compactable_messages_after_watermark(
                db,
                tenant_id=run.tenant_id,
                session_id=stored_run.session_id,
                covered_through_message_id=snapshot.covered_through_message_id,
            )
            return SessionCompactRequest(
                tenant_id=run.tenant_id,
                session_id=stored_run.session_id,
                source_agent_id=stored_run.agent_id,
                checkpoint_id=checkpoint.checkpoint_id,
                snapshot=snapshot,
                messages=messages,
                delta=delta,
            )

    @staticmethod
    def _expected_watermark(request: SessionCompactRequest) -> uuid.UUID | None:
        if not request.messages:
            return request.snapshot.covered_through_message_id
        message_id = request.messages[-1].get("id")
        if not isinstance(message_id, str):
            raise SessionContextCompletionError(
                "invalid_session_compact_message",
                "Session Compact input has no terminal message ID",
            )
        try:
            return uuid.UUID(message_id)
        except ValueError as exc:
            raise SessionContextCompletionError(
                "invalid_session_compact_message",
                "Session Compact input has an invalid terminal message ID",
            ) from exc

    async def _commit(
        self,
        *,
        run: RuntimeRunRecord,
        request: SessionCompactRequest,
        candidate: SessionContextCandidate,
    ) -> bool:
        expected_watermark = self._expected_watermark(request)
        if candidate.covered_through_message_id != expected_watermark:
            raise SessionContextCompletionError(
                "session_context_watermark_mismatch",
                "Session compactor did not preserve the deterministic message watermark",
            )

        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                    )
                    .with_for_update()
                )
                stored_run = result.scalar_one_or_none()
                if stored_run is None:
                    raise SessionContextCompletionError(
                        "run_not_found",
                        "terminal Session Context source Run does not exist",
                    )
                if self._receipt_state(stored_run, request.checkpoint_id):
                    return True
                if stored_run.session_id != request.session_id:
                    raise SessionContextCompletionError(
                        "session_context_source_changed",
                        "Run Session changed while Session Context was being compacted",
                    )
                current = await self._context_service.load_snapshot(
                    db,
                    tenant_id=run.tenant_id,
                    session_id=request.session_id,
                )
                if current != request.snapshot:
                    raise SessionContextConflict()
                await self._context_service.compare_and_swap(
                    db,
                    tenant_id=run.tenant_id,
                    session_id=request.session_id,
                    expected_version=request.snapshot.version,
                    expected_covered_through_message_id=(
                        request.snapshot.covered_through_message_id
                    ),
                    candidate=candidate,
                )
                stored_run.session_context_applied_checkpoint_id = request.checkpoint_id
                await db.flush()
                return True

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        delta = self._checkpoint_delta(run, checkpoint)
        if delta is None:
            return
        for _attempt in range(self._max_conflict_retries):
            request = await self._load_request(
                run=run,
                checkpoint=checkpoint,
                delta=delta,
            )
            if request is None:
                return
            candidate = await self._compactor.compact(request)
            try:
                if await self._commit(
                    run=run,
                    request=request,
                    candidate=candidate,
                ):
                    return
            except SessionContextConflict:
                continue
        raise SessionContextCompletionError(
            "session_context_conflict_limit",
            "Session Context kept changing while the terminal delta was merged",
        )


__all__ = [
    "SessionCompactRequest",
    "SessionContextCompactor",
    "SessionContextCompletionError",
    "SessionContextCompletionHandler",
]
