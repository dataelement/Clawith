"""Bounded process-local admission and fair quantum dispatch owned by Run."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.runtime.scheduler import FairReadyQueue, RunKey

logger = logging.getLogger(__name__)
Quantum = Callable[[RunKey], Awaitable[bool]]
FailureHandler = Callable[[RunKey, Exception], Awaitable[None]]


class ExecutionDispatcher:
    """True from a quantum requests tail re-entry, not a durable status transition.

    Reserve before Run creation; release only after creation rollback or terminal
    commit. Waiting keeps its reservation. Stop drains operations but deliberately
    retains reservations until the owner commits service-wide interruption.
    """

    def __init__(self, quantum: Quantum, on_failure: FailureHandler, *, slots: int = 50, capacity: int = 150) -> None:
        if type(slots) is not int or type(capacity) is not int or not 0 < slots <= capacity:
            raise ValueError("Execution capacity must contain all positive execution slots")
        self._quantum = quantum
        self._on_failure = on_failure
        self._slots = slots
        self._capacity = capacity
        self._held: dict[UUID, RunKey] = {}
        self._ready = FairReadyQueue(capacity=capacity)
        self._active: dict[UUID, asyncio.Task[None]] = {}
        self._wake_again: set[UUID] = set()
        self._changed = asyncio.Event()
        self._dispatch: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.failures: dict[UUID, str] = {}

    @property
    def admitted(self) -> int:
        return len(self._held)

    @property
    def active(self) -> int:
        return len(self._active)

    def is_admitted(self, key: RunKey) -> bool:
        return self._held.get(key.run_id) == key

    def reserved_keys(self) -> tuple[RunKey, ...]:
        """Bounded owner inventory; only confirmed terminal/absent facts allow release."""
        return tuple(self._held.values())

    def reserve(self, key: RunKey) -> bool:
        if self._stopping:
            raise RuntimeError("Execution intake is stopped")
        existing = self._held.get(key.run_id)
        if existing is not None:
            if existing != key:
                raise ValueError("Run admission cannot change ownership")
            return False
        if len(self._held) >= self._capacity:
            raise OverflowError("Run admission capacity is exhausted")
        self._held[key.run_id] = key
        return True

    def wake(self, key: RunKey) -> None:
        if self._held.get(key.run_id) != key:
            raise ValueError("Only an admitted Run can become ready")
        if self._stopping or key.run_id in self.failures:
            return
        if key.run_id in self._active:
            self._wake_again.add(key.run_id)
        else:
            self._ready.enqueue(key)
        self._changed.set()

    def release(self, key: RunKey) -> None:
        existing = self._held.get(key.run_id)
        if existing is None:
            return
        if existing != key:
            raise ValueError("Run release cannot change ownership")
        self._held.pop(key.run_id)
        self._ready.discard(key.run_id)
        self._wake_again.discard(key.run_id)
        self.failures.pop(key.run_id, None)
        task = self._active.get(key.run_id)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._changed.set()

    async def wait_released(self, key: RunKey) -> None:
        """Drain a cancelled operation before its owner removes continuation state."""
        if self.is_admitted(key):
            raise ValueError("Release the committed terminal Run before draining it")
        task = self._active.get(key.run_id)
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    def start(self) -> None:
        if self._dispatch is not None or self._stopping:
            raise RuntimeError("Execution dispatcher cannot be started twice")
        self._dispatch = asyncio.create_task(self._run(), name="run-dispatcher")

    async def _run(self) -> None:
        while not self._stopping:
            self._changed.clear()
            while len(self._active) < self._slots:
                key = self._ready.take()
                if key is None:
                    break
                self._active[key.run_id] = asyncio.create_task(self._execute(key), name=f"run-quantum-{key.run_id}")
            await self._changed.wait()

    async def _execute(self, key: RunKey) -> None:
        again = False
        failed = False
        try:
            again = await self._quantum(key)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — isolate defects in one Run at the execution boundary.
            failed = True
            # Only the owner may turn a quantum failure into a committed outcome.
            try:
                await self._on_failure(key, error)
            except Exception as settlement_error:  # noqa: BLE001 — retain capacity when authoritative settlement fails.
                if self.is_admitted(key):
                    self.failures[key.run_id] = type(settlement_error).__name__
                logger.error("Run failure could not be committed (%s)", type(settlement_error).__name__)
        finally:
            self._active.pop(key.run_id, None)
            requested = key.run_id in self._wake_again
            self._wake_again.discard(key.run_id)
            if not failed and not self._stopping and key.run_id in self._held and (again or requested):
                self._ready.enqueue(key)
            self._changed.set()

    def retry_settlement(self, key: RunKey) -> None:
        """Owner calls only with a retained outcome; never permission to repeat execution."""
        if self._held.get(key.run_id) != key:
            raise ValueError("Only an admitted Run can retry settlement")
        self.failures.pop(key.run_id, None)
        self.wake(key)

    async def stop(self) -> None:
        self._stopping = True
        self._changed.set()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._drain(), name="run-dispatcher-drain")
        cancelled = False
        while not self._stop_task.done():
            try:
                await asyncio.shield(self._stop_task)
            except asyncio.CancelledError:
                cancelled = True
        self._stop_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _drain(self) -> None:
        if self._dispatch is not None:
            await self._dispatch
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ready.clear()
        self._wake_again.clear()
