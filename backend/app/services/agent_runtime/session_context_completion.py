"""Exactly-once Session Context merging from terminal Runtime checkpoints."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
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
from app.services.agent_runtime.state import JsonObject, JsonValue


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


def _json_identity(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_unique_values(
    existing: Sequence[JsonValue],
    additions: Sequence[JsonValue],
) -> tuple[JsonValue, ...]:
    merged: list[JsonValue] = []
    identities: set[str] = set()
    for value in (*existing, *additions):
        identity = _json_identity(value)
        if identity in identities:
            continue
        identities.add(identity)
        merged.append(deepcopy(value))
    return tuple(merged)


def _merge_terminal_delta(
    snapshot: SessionContextSnapshot,
    delta: SessionContextDelta,
) -> SessionContextCandidate:
    resolved = {_json_identity(value) for value in delta.resolved_open_items}
    remaining_open_items = tuple(
        value
        for value in snapshot.open_items
        if _json_identity(value) not in resolved
    )
    summary = (
        f"{snapshot.summary}\n\n{delta.result_summary}"
        if snapshot.summary and snapshot.summary != delta.result_summary
        else delta.result_summary
    )
    return SessionContextCandidate(
        summary=summary,
        requirements=_merge_unique_values(
            snapshot.requirements,
            delta.new_requirements,
        ),
        decisions=_merge_unique_values(snapshot.decisions, delta.new_decisions),
        open_items=_merge_unique_values(remaining_open_items, delta.new_open_items),
        evidence_refs=_merge_unique_values(
            snapshot.evidence_refs,
            delta.evidence_refs,
        ),
        workspace_refs=_merge_unique_values(
            snapshot.workspace_refs,
            delta.workspace_refs,
        ),
        # A terminal delta contains structured Run facts, not a claim that any
        # public ChatMessage was compacted.  Only the background message-window
        # compactor may advance this watermark.
        covered_through_message_id=snapshot.covered_through_message_id,
    )


class SessionContextCompletionHandler:
    """Merge one terminal delta and its Run receipt in the same transaction."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        context_service: SessionContextService | None = None,
        max_conflict_retries: int = 3,
    ) -> None:
        if max_conflict_retries <= 0:
            raise ValueError("max_conflict_retries must be positive")
        self._session_factory = session_factory
        self._context_service = context_service or SessionContextService()
        self._max_conflict_retries = max_conflict_retries

    @staticmethod
    def _checkpoint_delta(
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> SessionContextDelta | None:
        if (
            run.session_id is not None
            and run.thread_id == run.session_id
        ):
            # D-015: a Direct Chat's LangGraph Thread is its only short-term
            # context truth. Group and other run-scoped Threads may still merge
            # their public Session delta here.
            return None
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
    ) -> tuple[uuid.UUID, SessionContextSnapshot, SessionContextCandidate] | None:
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
            return (
                stored_run.session_id,
                snapshot,
                _merge_terminal_delta(snapshot, delta),
            )

    async def _commit(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint_id: str,
        session_id: uuid.UUID,
        snapshot: SessionContextSnapshot,
        candidate: SessionContextCandidate,
    ) -> bool:
        if candidate.covered_through_message_id != snapshot.covered_through_message_id:
            raise SessionContextCompletionError(
                "session_context_watermark_mismatch",
                "terminal Session Context merge cannot advance the message watermark",
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
                if self._receipt_state(stored_run, checkpoint_id):
                    return True
                if stored_run.session_id != session_id:
                    raise SessionContextCompletionError(
                        "session_context_source_changed",
                        "Run Session changed while its terminal delta was being merged",
                    )
                current = await self._context_service.load_snapshot(
                    db,
                    tenant_id=run.tenant_id,
                    session_id=session_id,
                )
                if current != snapshot:
                    raise SessionContextConflict()
                await self._context_service.compare_and_swap(
                    db,
                    tenant_id=run.tenant_id,
                    session_id=session_id,
                    expected_version=snapshot.version,
                    expected_covered_through_message_id=(
                        snapshot.covered_through_message_id
                    ),
                    candidate=candidate,
                )
                stored_run.session_context_applied_checkpoint_id = checkpoint_id
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
            session_id, snapshot, candidate = request
            try:
                if await self._commit(
                    run=run,
                    checkpoint_id=checkpoint.checkpoint_id,
                    session_id=session_id,
                    snapshot=snapshot,
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
