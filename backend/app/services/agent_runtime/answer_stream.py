"""Coalesced durable observations for provisional user-visible answer text."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.command_worker import RuntimeSessionFactory

_DEFAULT_FLUSH_INTERVAL_SECONDS = 0.1
_DEFAULT_MAX_BUFFER_CHARS = 512


class AnswerStreamWriter:
    """Buffer visible answer deltas and persist short, idempotent observations."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        agent_id: uuid.UUID,
        attempt_id: uuid.UUID | str,
        flush_interval: float = _DEFAULT_FLUSH_INTERVAL_SECONDS,
        max_buffer_chars: int = _DEFAULT_MAX_BUFFER_CHARS,
    ) -> None:
        if flush_interval <= 0:
            raise ValueError("flush_interval must be positive")
        if max_buffer_chars <= 0:
            raise ValueError("max_buffer_chars must be positive")
        normalized_attempt_id = str(attempt_id).strip()
        if not normalized_attempt_id:
            raise ValueError("attempt_id must be non-empty")

        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._agent_id = agent_id
        self._attempt_id = normalized_attempt_id
        self._flush_interval = flush_interval
        self._max_buffer_chars = max_buffer_chars
        self._parts: list[str] = []
        self._buffer_chars = 0
        self._next_sequence = 1
        self._closed = False
        self._visible_started = False
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    @property
    def visible_started(self) -> bool:
        """Whether at least one visible observation was transactionally written."""
        return self._visible_started

    async def write(self, content: str) -> None:
        """Accept one visible text delta without waiting for database I/O."""
        if self._closed:
            raise RuntimeError("answer stream writer is closed")
        if not isinstance(content, str):
            raise TypeError("answer stream content must be text")
        if not content:
            return

        self._parts.append(content)
        self._buffer_chars += len(content)
        self._ensure_worker()
        if self._buffer_chars >= self._max_buffer_chars:
            self._wake.set()

    async def flush(self) -> None:
        """Flush all currently buffered content."""
        if self._parts:
            self._ensure_worker()
            self._wake.set()
        worker = self._worker
        if worker is not None and not worker.done():
            await worker

    async def close(self) -> None:
        """Stop accepting content and flush the final buffered delta."""
        if self._closed:
            return
        self._closed = True
        if self._parts:
            self._ensure_worker()
            self._wake.set()
        worker = self._worker
        if worker is not None and not worker.done():
            await worker

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.done():
            try:
                self._worker.result()
            except Exception as exc:
                # The failed batch was restored to the buffer and the next
                # worker retries the same deterministic sequence.
                logger.warning(
                    "[RuntimeAnswerStream] retrying restored batch after {}",
                    type(exc).__name__,
                )
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        while self._parts:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval)
            except TimeoutError:
                pass
            self._wake.clear()
            await self._flush_once()

    async def _flush_once(self) -> None:
        if not self._parts:
            return

        parts = self._parts
        content = "".join(parts)
        sequence = self._next_sequence
        self._parts = []
        self._buffer_chars = 0
        key = f"answer-stream:{self._attempt_id}:{sequence}"

        try:
            async with self._session_factory() as db, db.begin():
                await db.execute(
                    insert(AgentRunEvent)
                    .values(
                        id=uuid.uuid5(
                            self._run_id,
                            f"answer-stream-event:{self._attempt_id}:{sequence}",
                        ),
                        tenant_id=self._tenant_id,
                        run_id=self._run_id,
                        agent_id=self._agent_id,
                        event_type="status_changed",
                        summary="Assistant answer streaming",
                        payload={
                            "activity_type": "assistant_delta",
                            "status": "running",
                            "attempt_id": self._attempt_id,
                            "sequence": sequence,
                            "content": content,
                            "reset": sequence == 1,
                        },
                        artifact_refs=[],
                        idempotency_key=key,
                        source_checkpoint_id=None,
                        created_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing()
                )
        except BaseException:
            self._parts = [*parts, *self._parts]
            self._buffer_chars += len(content)
            raise

        self._next_sequence += 1
        self._visible_started = True
