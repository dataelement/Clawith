"""One process-local Run executor; durable decisions remain in RunService."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.context.public import (
    ContextAssembler,
    ContextProjectionService,
    ContextSource,
    ContextState,
    ContextSummarizer,
    ContextTelemetry,
    ContextTokenCounter,
    ContextUnit,
    ModelPreparationFailure,
    restore_base,
)
from app.modules.model.public import (
    ModelContent,
    ModelExecutionService,
    ModelFailure,
    ModelMessage,
    ModelStepRequest,
    ModelStreamEvent,
    ModelToolCall,
    ModelToolDefinition,
)
from app.modules.run.contracts import (
    ContextBasePayload,
    InitialInputPayload,
    InputContent,
    InvalidHistory,
    ModelInputPayload,
    ModelStepPayload,
    RelatedInputPayload,
    ToolResultPayload,
    WaitingPayload,
    encode_history,
)
from app.modules.run.lifecycle import (
    HistoryFragment,
    OutcomeConsumer,
    RunService,
    RunView,
    StartConsumer,
    StartResult,
    TransitionResult,
    WaitingConsumer,
)
from app.modules.run.repository import HistoryEntry, SourceIdentity
from app.modules.run.snapshot import RunSnapshot, derive_child, model_visible_prefix
from app.modules.tool.public import AvailableToolSet, ToolResult, tool_result_content
from app.runtime.dispatcher import ExecutionDispatcher
from app.runtime.scheduler import RunKey

logger = logging.getLogger(__name__)
_TERMINAL = ("Completed", "Failed", "Cancelled", "Interrupted")
_MODEL_RETRY_DELAYS = (0.25, 0.5)


@dataclass(frozen=True, slots=True)
class RunStreamEvent:
    step_id: str
    attempt: int
    kind: Literal["attempt_started", "model_event", "attempt_discarded"]
    event: ModelStreamEvent | None = None


RunStreamObserver = Callable[[RunKey, RunStreamEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ToolBatchOutcome:
    results: tuple[ToolResult, ...]
    available: AvailableToolSet
    wait_for_related: bool = False


class ToolBatchPort(Protocol):
    async def execute(self, *, snapshot: RunSnapshot, step_id: str, available: AvailableToolSet,
            calls: tuple[ModelToolCall, ...]) -> ToolBatchOutcome: ...


@dataclass(slots=True)
class _Cache:
    snapshot: RunSnapshot
    assembler: ContextAssembler
    available: AvailableToolSet
    state: ContextState
    cursor: int = 0
    base_sequence: int | None = None
    additions: list[ContextUnit] = field(default_factory=list)
    exchange: ModelStepPayload | None = None
    exchange_messages: list[ModelMessage] = field(default_factory=list)
    deferred_inputs: list[tuple[int, ModelMessage]] = field(default_factory=list)
    inflight: ModelInputPayload | None = None
    exchange_visible: frozenset[str] = frozenset()
    result_ids: set[str] = field(default_factory=set)
    todo: str | None = None
    addition_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _ModelCommit:
    payload: ModelStepPayload


@dataclass(frozen=True, slots=True)
class _ToolCommit:
    step: ModelStepPayload
    outcome: ToolBatchOutcome
    wait_question: str | None


@dataclass(frozen=True, slots=True)
class _FailureCommit:
    reason: str


@dataclass(frozen=True, slots=True)
class _ModelAttempt:
    snapshot: RunSnapshot
    request: ModelStepRequest
    read_through_sequence: int
    number: int = 1


@dataclass(frozen=True, slots=True)
class _PreparationAttempt:
    cache: _Cache
    state: ContextState
    additions: tuple[ContextUnit, ...]
    tools: tuple[ModelToolDefinition, ...]
    todo: str | None
    minute: datetime | None
    read_through_sequence: int
    number: int = 1


@dataclass(frozen=True, slots=True)
class _Starting:
    agent_id: UUID
    parent_run_id: UUID | None
    result: asyncio.Future[StartResult]


def _key(view: RunView) -> RunKey:
    return RunKey(view.tenant_id, view.agent_id, view.id)


class RunRuntime:
    def __init__(self, *, control_sessions: async_sessionmaker[AsyncSession],
            execution_sessions: async_sessionmaker[AsyncSession], model: ModelExecutionService,
            tools: ToolBatchPort, consumer: OutcomeConsumer | None = None,
            start_consumer: StartConsumer | None = None, waiting_consumer: WaitingConsumer | None = None,
            observer: RunStreamObserver | None = None,
            context_observer: Callable[[RunKey, ContextTelemetry], None] | None = None,
            summarizer_factory: Callable[[RunSnapshot], ContextSummarizer] | None = None,
            token_counter_factory: Callable[[RunSnapshot], ContextTokenCounter] | None = None,
            slots: int = 50, capacity: int = 150) -> None:
        self._control = control_sessions
        self._execution = execution_sessions
        self._model = model
        self._tools = tools
        self._consumer = consumer
        self._start_consumer = start_consumer
        self._waiting_consumer = waiting_consumer
        self._observer = observer
        self._context_observer = context_observer
        self._summarizer_factory = summarizer_factory
        self._token_counter_factory = token_counter_factory
        self.dispatcher = ExecutionDispatcher(self.quantum, self.on_failure, slots=slots, capacity=capacity)
        self._starting: dict[tuple[UUID, str, UUID, str], _Starting] = {}
        self._start_capacity = capacity
        self._caches: dict[UUID, _Cache] = {}
        self._pending: dict[UUID, _ModelCommit | _ToolCommit | _FailureCommit] = {}
        self._attempts: dict[UUID, _ModelAttempt | _PreparationAttempt] = {}
        self._accepting = False
        self._started = False
        self._closed = False
        self._closing: asyncio.Task[None] | None = None
        self.observer_failures = 0
        self.context_observer_failures = 0

    async def startup(self) -> None:
        if self._started:
            raise RuntimeError("Run Runtime cannot start twice")
        await self._interrupt_all()
        self.dispatcher.start()
        self._started = True
        self._accepting = True

    async def close(self) -> None:
        self._accepting = False
        if self._closing is None:
            self._closing = asyncio.create_task(self._close(), name="run-runtime-close")
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        self._closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        self._accepting = False
        await self.dispatcher.stop()
        if self._starting:
            await asyncio.gather(*(asyncio.shield(entry.result) for entry in tuple(self._starting.values())),
                return_exceptions=True)
        await self._interrupt_all()
        for key in self.dispatcher.reserved_keys():
            self.dispatcher.release(key)
        self._caches.clear()
        self._pending.clear()
        self._attempts.clear()
        self._closed = True

    async def start(self, *, snapshot: RunSnapshot, input: InputContent, source: SourceIdentity,
            parent_run_id: UUID | None = None) -> StartResult:
        run_id = snapshot.workspace.run_id
        if run_id is None:
            raise InvalidInput("Run Snapshot requires a Run identity")
        key = RunKey(snapshot.tenant_id, snapshot.agent_id, run_id)
        self._intake()
        identity = (snapshot.tenant_id, source.kind, source.owner_id, source.key)
        starting = self._starting.get(identity)
        if starting is not None:
            if (starting.agent_id, starting.parent_run_id) != (snapshot.agent_id, parent_run_id):
                raise Conflict("Run source belongs to another execution scope")
            result = await asyncio.shield(starting.result)
            return StartResult(result.run, False)
        if len(self._starting) >= self._start_capacity:
            raise Conflict("Run start intake is full; retry after pending starts settle")
        starting = _Starting(snapshot.agent_id, parent_run_id, asyncio.get_running_loop().create_future())
        self._starting[identity] = starting
        try:
            result = await self._start_one(snapshot=snapshot, input=input, source=source,
                parent_run_id=parent_run_id, key=key)
        except asyncio.CancelledError:
            starting.result.cancel()
            raise
        except BaseException as error:
            starting.result.set_exception(error)
            starting.result.exception()  # The leader already observes this exception; followers may still await it.
            raise
        else:
            starting.result.set_result(result)
            return result
        finally:
            if self._starting.get(identity) is starting:
                del self._starting[identity]

    async def _start_one(self, *, snapshot: RunSnapshot, input: InputContent, source: SourceIdentity,
            parent_run_id: UUID | None, key: RunKey) -> StartResult:
        reserved = False
        def admit() -> None:
            nonlocal reserved
            self._intake()
            try:
                acquired = self.dispatcher.reserve(key)
            except OverflowError:
                raise Conflict("Run admission is full; retry after existing work finishes") from None
            if not acquired:
                raise Conflict("Run identity already belongs to admitted work")
            reserved = True
        try:
            async with transaction(self._control) as tx:
                result = await RunService(tx).start(tenant_id=snapshot.tenant_id, agent_id=snapshot.agent_id,
                    run_id=key.run_id, snapshot=snapshot, input=input, source=source, parent_run_id=parent_run_id, admit=admit,
                    start_consumer=self._start_consumer)
        except BaseException:
            if reserved:
                cleanup = asyncio.create_task(self._reconcile_failed_start(key, source, parent_run_id),
                    name=f"run-start-reconcile-{key.run_id}")
                cancelled = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
                if cancelled:
                    raise asyncio.CancelledError
            raise
        if not result.created:
            if reserved:
                self.dispatcher.release(key)
            return result
        try:
            self.dispatcher.wake(key)
        except Exception:
            # Creation already committed. Never release admission before the compensating terminal commit.
            async with transaction(self._control) as tx:
                interrupted = await RunService(tx).terminate(tenant_id=key.tenant_id, run_id=key.run_id,
                    status="Interrupted", reason="initial_enqueue_failed", consumer=self._consumer if snapshot.role == "main" else None)
            await self._apply(interrupted)
            raise
        return result

    async def _reconcile_failed_start(self, key: RunKey, source: SourceIdentity, parent_run_id: UUID | None) -> None:
        """Transaction exit can fail after COMMIT; retain admission until its actual outcome is known."""
        changed = None
        async with transaction(self._control) as tx:
            service = RunService(tx)
            try:
                view = await service.get(tenant_id=key.tenant_id, run_id=key.run_id)
            except NotFound:
                view = None
            if view is not None and (view.agent_id, view.parent_run_id, view.source) == (
                    key.agent_id, parent_run_id, source):
                changed = await service.terminate(tenant_id=key.tenant_id, run_id=key.run_id,
                    status="Interrupted", reason="start_transaction_exit_failed",
                    consumer=self._consumer if parent_run_id is None else None)
        if changed is None:
            self.dispatcher.release(key)
        else:
            await self._apply(changed)

    async def input(self, *, tenant_id: UUID, run_id: UUID, input: InputContent,
            source: SourceIdentity, waiting_reference: str | None = None) -> TransitionResult:
        self._intake()
        async with transaction(self._control) as tx:
            changed = await RunService(tx).append_related(tenant_id=tenant_id, run_id=run_id,
                input=input, source=source, waiting_reference=waiting_reference)
        await self._apply(changed)
        return changed

    async def cancel(self, *, tenant_id: UUID, run_id: UUID, reason: str = "cancelled") -> TransitionResult:
        self._intake()
        async with transaction(self._control) as tx:
            service = RunService(tx)
            view = await service.get(tenant_id=tenant_id, run_id=run_id)
            changed = await service.terminate(tenant_id=tenant_id, run_id=run_id,
                status="Cancelled", reason=reason, consumer=self._consumer if view.parent_run_id is None else None)
        await self._apply(changed)
        return changed

    async def delegate(self, *, tenant_id: UUID, parent_run_id: UUID, step_id: str, call_id: str,
            work: str) -> UUID:
        async with transaction(self._control) as tx:
            service = RunService(tx)
            await service.verify_task_origin(tenant_id=tenant_id, parent_run_id=parent_run_id, step_id=step_id, call_id=call_id)
            parent_snapshot = await service.read_snapshot(tenant_id=tenant_id, run_id=parent_run_id)
        snapshot = derive_child(parent_snapshot, run_id=uuid4())
        result = await self.start(snapshot=snapshot, input=InputContent(work), parent_run_id=parent_run_id,
            source=SourceIdentity("task", parent_run_id, sha256(f"{step_id}\0{call_id}".encode()).hexdigest()))
        return result.run.id

    async def resume(self, *, tenant_id: UUID, parent_run_id: UUID, child_run_id: UUID, step_id: str,
            call_id: str, waiting_reference: str, answer: str) -> TransitionResult:
        await self._owned_child(tenant_id, parent_run_id, child_run_id)
        async with transaction(self._control) as tx:
            await RunService(tx).verify_task_origin(tenant_id=tenant_id, parent_run_id=parent_run_id,
                step_id=step_id, call_id=call_id)
        return await self.input(tenant_id=tenant_id, run_id=child_run_id, input=InputContent(answer),
            source=SourceIdentity("parent_answer", parent_run_id, sha256(f"{step_id}\0{call_id}".encode()).hexdigest()),
            waiting_reference=waiting_reference)

    async def inspect_fragment(self, *, tenant_id: UUID, parent_run_id: UUID, child_run_id: UUID,
            after_sequence: int = 0, content_offset: int = 0, max_characters: int = 16000) -> HistoryFragment | None:
        await self._owned_child(tenant_id, parent_run_id, child_run_id)
        async with transaction(self._control) as tx:
            return await RunService(tx).read_history_fragment(tenant_id=tenant_id, run_id=child_run_id,
                after_sequence=after_sequence, content_offset=content_offset, max_characters=max_characters)

    async def _owned_child(self, tenant_id: UUID, parent_run_id: UUID, child_run_id: UUID) -> None:
        async with transaction(self._control) as tx:
            service = RunService(tx)
            parent = await service.get(tenant_id=tenant_id, run_id=parent_run_id)
            child = await service.get(tenant_id=tenant_id, run_id=child_run_id)
            if parent.parent_run_id is not None:
                raise InvalidInput("Task inspection requires a Main Run")
            if child.parent_run_id != parent_run_id:
                raise InvalidInput("Task history belongs to another Parent Run")

    async def retry_settlement(self, *, tenant_id: UUID, run_id: UUID) -> None:
        self._intake()
        if run_id not in self._pending:
            raise InvalidInput("Run has no retained settlement to retry")
        async with transaction(self._control) as tx:
            view = await RunService(tx).get(tenant_id=tenant_id, run_id=run_id)
        self.dispatcher.retry_settlement(_key(view))

    async def quantum(self, key: RunKey) -> bool:
        if key.run_id in self._pending:
            return await self._commit_pending(key)
        if key.run_id in self._attempts:
            work = self._attempts[key.run_id]
            return await self._prepare_model(key, work) if isinstance(work, _PreparationAttempt) else await self._model_attempt(key, work)
        async with transaction(self._execution) as tx:
            service = RunService(tx)
            view = await service.get(tenant_id=key.tenant_id, run_id=key.run_id)
            if view.status != "Running":
                return False
            cache = self._caches.get(key.run_id)
            if cache is None:
                cache = await self._load(service, key, tx)
                self._caches[key.run_id] = cache
            await self._advance(service, key, cache, view.latest_history_sequence)
        if cache.exchange is not None:
            missing = tuple(call for call in cache.exchange.result.calls if call.call_id not in cache.result_ids)
            result = await self._tools.execute(snapshot=cache.snapshot, step_id=cache.exchange.step_id,
                available=cache.available, calls=missing)
            if (result.available.tenant_id, result.available.agent_id, result.available.tools) != (
                    cache.available.tenant_id, cache.available.agent_id, cache.available.tools):
                raise InvalidInput("Tool batch cannot change captured authorization")
            if len(result.results) != len(missing) or {item.call_id for item in result.results} != {call.call_id for call in missing}:
                raise InvalidInput("Tool batch must settle every submitted call exactly once")
            names = {call.call_id: call.name for call in missing}
            try:
                for item in result.results:
                    encode_history(ToolResultPayload(cache.exchange.step_id, names[item.call_id], item))
                wait_question = self._wait_question(result.results, names)
                if result.wait_for_related and wait_question is None:
                    wait_question = ""
                if wait_question is not None:
                    encode_history(WaitingPayload(cache.exchange.step_id, cache.exchange.step_id,
                        wait_question, cache.exchange.read_through_sequence, result.wait_for_related and not wait_question))
            except (InvalidHistory, InvalidInput):
                self._pending[key.run_id] = _FailureCommit("invalid_tool_result")
            else:
                self._pending[key.run_id] = _ToolCommit(cache.exchange, result, wait_question)
            return await self._commit_pending(key)
        if cache.inflight is not None:
            raise InvalidInput("Run History has an unfinished Model request")
        definitions = tuple(ModelToolDefinition(item.spec.name, item.spec.description, item.spec.input_schema_json)
            for item in cache.available.visible())
        minute = datetime.now(UTC).replace(second=0, microsecond=0) if cache.snapshot.include_current_time else None
        work = _PreparationAttempt(cache, cache.state, tuple(cache.additions), definitions, cache.todo, minute, cache.cursor)
        self._attempts[key.run_id] = work
        return await self._prepare_model(key, work)

    async def _prepare_model(self, key: RunKey, work: _PreparationAttempt) -> bool:
        if work.number > 1:
            await asyncio.sleep(_MODEL_RETRY_DELAYS[work.number - 2])
        cache, definitions, minute = work.cache, work.tools, work.minute
        try:
            prepared = await cache.assembler.prepare(state=work.state, additions=work.additions,
                tools=definitions, todo=work.todo, minute_time=minute)
        except ModelPreparationFailure as error:
            if self._retryable(error.failure, work.number):
                self._attempts[key.run_id] = replace(work, number=work.number + 1)
                return True
            self._attempts.pop(key.run_id, None)
            self._pending[key.run_id] = _FailureCommit(error.failure.code)
            return await self._commit_pending(key)
        self._attempts.pop(key.run_id, None)
        if self._context_observer is not None:
            try:
                self._context_observer(key, prepared.telemetry)
            except Exception as error:  # noqa: BLE001 -- only the optional synchronous measurement sink is isolated.
                self.context_observer_failures += 1
                logger.warning("Run Context observer failed: %s", type(error).__name__)
        step_id = str(uuid4())
        base_sequence = cache.base_sequence
        async with transaction(self._execution) as tx:
            service = RunService(tx)
            if prepared.observation is not None:
                base = prepared.observation
                record = await service.record_history(tenant_id=key.tenant_id, run_id=key.run_id,
                    payload=ContextBasePayload(base.messages, base.state.coverage_sequence, base.state.through_sequence),
                    source=SourceIdentity("context_base", key.run_id, step_id))
                base_sequence = record.entry.sequence
            projection_hash = await ContextProjectionService(tx).save_prepared(tenant_id=key.tenant_id, run_id=key.run_id, prepared=prepared)
            if not prepared.telemetry.compactions:
                await service.record_history(tenant_id=key.tenant_id, run_id=key.run_id,
                    payload=ModelInputPayload(step_id, base_sequence, work.read_through_sequence,
                        tuple(item.name for item in definitions), minute.isoformat(timespec="minutes") if minute else None,
                        projection_hash),
                    source=SourceIdentity("model_input", key.run_id, step_id))
        cache.state, cache.additions, cache.base_sequence = prepared.state, [], base_sequence
        cache.addition_bytes = 0
        if prepared.telemetry.compactions:
            # Summary generation consumed this quantum's Model operation; the primary call gets the next turn.
            return True
        attempt = _ModelAttempt(cache.snapshot,
            ModelStepRequest(key.run_id, step_id, prepared.messages, definitions, prepared.input_tokens,
                prepared.output_tokens, stream=cache.snapshot.model.profile.supports_streaming), work.read_through_sequence)
        self._attempts[key.run_id] = attempt
        return await self._model_attempt(key, attempt)

    @staticmethod
    def _retryable(failure: ModelFailure, number: int) -> bool:
        return (not failure.unrecoverable
            and failure.code in ("transport_failed", "rate_limited", "provider_unavailable")
            and number <= len(_MODEL_RETRY_DELAYS))

    async def _model_attempt(self, key: RunKey, attempt: _ModelAttempt) -> bool:
        if attempt.number > 1:
            await asyncio.sleep(_MODEL_RETRY_DELAYS[attempt.number - 2])
        observer_enabled = True
        async def emit(event: RunStreamEvent) -> None:
            nonlocal observer_enabled
            if self._observer is None or not observer_enabled:
                return
            try:
                async with asyncio.timeout(0.1):
                    await self._observer(key, event)
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                observer_enabled = False
                self.observer_failures += 1
                logger.warning("Run stream observer disconnected: CancelledError")
            except Exception as error:  # noqa: BLE001 -- only the optional display observer is isolated.
                observer_enabled = False
                self.observer_failures += 1
                logger.warning("Run stream observer disconnected: %s", type(error).__name__)

        async def observe(event: ModelStreamEvent) -> None:
            await emit(RunStreamEvent(attempt.request.step_id, attempt.number, "model_event", event))
        await emit(RunStreamEvent(attempt.request.step_id, attempt.number, "attempt_started"))
        result = await self._model.execute_step(attempt.snapshot.model.policy, attempt.request,
            on_event=observe if self._observer is not None else None)
        if isinstance(result, ModelFailure):
            await emit(RunStreamEvent(attempt.request.step_id, attempt.number, "attempt_discarded"))
            if self._retryable(result, attempt.number):
                self._attempts[key.run_id] = replace(attempt, number=attempt.number + 1)
                return True
            self._attempts.pop(key.run_id, None)
            self._pending[key.run_id] = _FailureCommit(result.code)
        else:
            self._attempts.pop(key.run_id, None)
            payload = ModelStepPayload(attempt.request.step_id, attempt.read_through_sequence, result)
            try:
                encode_history(payload)
            except InvalidHistory:
                self._pending[key.run_id] = _FailureCommit("invalid_model_result")
            else:
                self._pending[key.run_id] = _ModelCommit(payload)
        return await self._commit_pending(key)

    async def _commit_pending(self, key: RunKey) -> bool:
        pending = self._pending[key.run_id]
        changed = None
        try:
            async with transaction(self._execution) as tx:
                service = RunService(tx)
                view = await service.get(tenant_id=key.tenant_id, run_id=key.run_id, lock=True)
                consumer = self._consumer if view.parent_run_id is None else None
                if view.status in _TERMINAL:
                    changed = TransitionResult(view, False)
                elif isinstance(pending, _FailureCommit):
                    changed = await service.terminate(tenant_id=key.tenant_id, run_id=key.run_id,
                        status="Failed", reason=pending.reason, consumer=consumer)
                elif isinstance(pending, _ModelCommit):
                    await service.record_model_step(tenant_id=key.tenant_id, run_id=key.run_id, payload=pending.payload)
                    result = pending.payload.result
                    failure = None
                    if result.finish_reason in ("length", "content_filter"):
                        failure = f"model_finish_{result.finish_reason}"
                    elif bool(result.calls) != (result.finish_reason == "tool_calls"):
                        failure = "model_finish_protocol"
                    elif result.finish_reason == "refusal" and not result.content.strip():
                        failure = "model_refusal"
                    if failure is not None:
                        changed = await service.terminate(tenant_id=key.tenant_id, run_id=key.run_id,
                            status="Failed", reason=failure, consumer=consumer)
                    elif not result.calls:
                        changed = await service.complete(tenant_id=key.tenant_id, run_id=key.run_id,
                            step_id=pending.payload.step_id, output=result.content, consumer=consumer)
                else:
                    names = {call.call_id: call.name for call in pending.step.result.calls}
                    for result in pending.outcome.results:
                        await service.record_tool_result(tenant_id=key.tenant_id, run_id=key.run_id,
                            payload=ToolResultPayload(pending.step.step_id, names[result.call_id], result))
                    if pending.wait_question is not None:
                        changed = await service.wait(tenant_id=key.tenant_id, run_id=key.run_id,
                            payload=WaitingPayload(pending.step.step_id, pending.step.step_id, pending.wait_question,
                                pending.step.read_through_sequence, pending.outcome.wait_for_related and not pending.wait_question),
                            waiting_consumer=self._waiting_consumer)
        except SQLAlchemyError:
            # Retain the already-produced Model/Tool result; only this transaction is retried.
            await asyncio.sleep(0.05)
            return True
        self._pending.pop(key.run_id, None)
        cache = self._caches.get(key.run_id)
        if isinstance(pending, _ToolCommit) and cache is not None:
            cache.available = pending.outcome.available
        if changed is not None:
            await self._apply(changed)
            return changed.run.status == "Running"
        return True

    async def on_failure(self, key: RunKey, error: Exception) -> None:
        if key.run_id in self._pending:
            # An outcome already exists. Hold it for explicit settlement retry, never re-execute its side effect.
            raise error
        self._pending[key.run_id] = _FailureCommit(type(error).__name__)
        await self._commit_pending(key)
        if key.run_id in self._pending:
            raise RuntimeError("Run failure settlement requires retry")

    @staticmethod
    def _wait_question(results: tuple[ToolResult, ...], names: dict[str, str]) -> str | None:
        questions = []
        waiting = False
        for result in results:
            if result.status != "success" or names[result.call_id] not in ("need_input", "wait_for_tasks"):
                continue
            value = json.loads(result.content_json)
            if names[result.call_id] == "need_input" and value.get("need_input") is True:
                question = value.get("question")
                if not isinstance(question, str) or not question.strip():
                    raise InvalidInput("Need Input requires a question")
                questions.append(question)
                waiting = True
            if names[result.call_id] == "wait_for_tasks" and value.get("wait_for_tasks") is True:
                waiting = True
        return "\n".join(questions) if waiting else None

    async def _load(self, service: RunService, key: RunKey, transaction_context: TransactionContext) -> _Cache:
        snapshot = await service.read_snapshot(tenant_id=key.tenant_id, run_id=key.run_id)
        initial = (await service.read_history(tenant_id=key.tenant_id, run_id=key.run_id,
            after_sequence=0, through_sequence=1, limit=1)).entries[0]
        if not isinstance(initial.payload, InitialInputPayload) or initial.source is None:
            raise InvalidHistory("Run requires its original initial input at sequence one")
        sources = tuple(ContextSource(f"{section.category}:{section.source}", section.content,
            "system" if section.category in ("platform", "agent") else "user") for section in model_visible_prefix(snapshot))
        sources += (ContextSource(f"initial_input:{initial.source.kind}:{initial.source.owner_id}:{initial.source.key}:history:1",
            self._input_text(initial.payload.input), "user"),)
        available = snapshot.tools.for_role(snapshot.role, direct_names=snapshot.initial_direct_names)
        summarizer = self._summarizer_factory(snapshot) if self._summarizer_factory is not None else None
        counter = self._token_counter_factory(snapshot) if self._token_counter_factory is not None else None
        assembler = ContextAssembler(sources=sources, profile=snapshot.model.profile, summarizer=summarizer,
            token_counter=counter,
            model_limits=self._model.operation_limits,
            request_overhead_bytes=32768 + len(snapshot.model.policy.settings_json.encode())
                + len(snapshot.model.policy.capabilities_json.encode()))
        cache = _Cache(snapshot, assembler, available, ContextState(), cursor=1)
        base = await service.latest_fact(tenant_id=key.tenant_id, run_id=key.run_id, kind="context_base")
        if base is not None:
            if not isinstance(base.payload, ContextBasePayload):
                raise InvalidInput("Context base has an invalid History payload")
            cache.state = restore_base(messages=base.payload.messages, coverage_sequence=base.payload.coverage_sequence,
                through_sequence=base.payload.through_sequence)
            cache.cursor, cache.base_sequence = max(1, base.payload.through_sequence), base.sequence
        exposed = await service.latest_fact(tenant_id=key.tenant_id, run_id=key.run_id, kind="model_input")
        if exposed is not None:
            if not isinstance(exposed.payload, ModelInputPayload):
                raise InvalidInput("Model input has an invalid History payload")
            cache.available = cache.available.expose(frozenset(exposed.payload.visible_tool_names))
            projection = (await ContextProjectionService(transaction_context).load(tenant_id=key.tenant_id, run_id=key.run_id,
                expected_hash=exposed.payload.context_state_hash)) if exposed.payload.context_state_hash is not None else None
            latest = await service.get(tenant_id=key.tenant_id, run_id=key.run_id)
            if (projection is not None and exposed.payload.context_state_hash is not None
                    and exposed.payload.base_sequence == cache.base_sequence
                    and projection.coverage_sequence == cache.state.coverage_sequence
                    and cache.state.through_sequence <= projection.through_sequence <= exposed.payload.read_through_sequence
                    and exposed.payload.read_through_sequence <= latest.latest_history_sequence):
                cache.state, cache.cursor = projection, max(1, projection.through_sequence)
        todo = await service.latest_fact(tenant_id=key.tenant_id, run_id=key.run_id, kind="tool_result", successful_tool_name="todo")
        if todo is not None and isinstance(todo.payload, ToolResultPayload):
            cache.todo = todo.payload.result.content_json
        return cache

    async def _advance(self, service: RunService, key: RunKey, cache: _Cache, through: int) -> None:
        while cache.cursor < through:
            page = await service.read_history(tenant_id=key.tenant_id, run_id=key.run_id,
                after_sequence=cache.cursor, through_sequence=through)
            for entry in page.entries:
                self._consume(cache, entry)
                cache.cursor = entry.sequence
        if cache.inflight is None and cache.exchange is None:
            self._flush_inputs(cache)

    @staticmethod
    def _input_text(input: InputContent) -> str:
        return input.text + "".join(f"\nReference: {ref.reference}" for ref in input.references)

    @staticmethod
    def _flush_inputs(cache: _Cache, through: int | None = None) -> None:
        retained = []
        for sequence, message in cache.deferred_inputs:
            if through is None or sequence <= through:
                cache.additions.append(ContextUnit(sequence, (message,)))
            else:
                retained.append((sequence, message))
        cache.deferred_inputs = retained

    @staticmethod
    def _retain(cache: _Cache, message: ModelMessage) -> None:
        size = 256 + sum(256 + len(content.value.encode()) for content in message.content)
        size += sum(256 + len(call.call_id.encode()) + len(call.name.encode()) + len(call.arguments_json.encode())
            for call in message.calls)
        if cache.addition_bytes + size > 16 * 1024 * 1024:
            raise InvalidInput("Run Context source batch exceeds its physical assembly bound")
        cache.addition_bytes += size

    @staticmethod
    def _consume(cache: _Cache, entry: HistoryEntry) -> None:
        payload = entry.payload
        if isinstance(payload, InitialInputPayload):
            raise InvalidHistory("Initial input cannot appear again in the execution tail")
        if isinstance(payload, RelatedInputPayload):
            message = ModelMessage("user", (ModelContent("text", f"[Run input {entry.sequence}]\n{RunRuntime._input_text(payload.input)}"),))
            RunRuntime._retain(cache, message)
            cache.deferred_inputs.append((entry.sequence, message))
        elif isinstance(payload, ModelStepPayload):
            if cache.exchange is not None:
                raise InvalidInput("Run History contains overlapping Tool exchanges")
            if cache.inflight is not None and (cache.inflight.step_id, cache.inflight.read_through_sequence) != (
                    payload.step_id, payload.read_through_sequence):
                raise InvalidHistory("Model Step does not match its observed request")
            visible = frozenset(cache.inflight.visible_tool_names) if cache.inflight is not None else cache.available.direct_names
            RunRuntime._flush_inputs(cache, payload.read_through_sequence)
            cache.inflight = None
            message = ModelMessage("assistant", (ModelContent("text", payload.result.content),),
                calls=payload.result.calls, interaction_id=payload.result.interaction_id,
                requires_continuation=payload.result.requires_continuation)
            RunRuntime._retain(cache, message)
            if payload.result.calls:
                cache.exchange = payload
                cache.exchange_visible = visible
                cache.exchange_messages = [message]
                cache.result_ids.clear()
            else:
                cache.additions.append(ContextUnit(entry.sequence, (message,) + tuple(part for _, part in cache.deferred_inputs)))
                cache.deferred_inputs.clear()
        elif isinstance(payload, ToolResultPayload):
            if cache.exchange is None or cache.exchange.step_id != payload.step_id:
                raise InvalidInput("Tool Result has no matching Context exchange")
            expected = {call.call_id: call.name for call in cache.exchange.result.calls}
            if expected.get(payload.result.call_id) != payload.tool_name or payload.result.call_id in cache.result_ids:
                raise InvalidInput("Tool Result does not match its unique Context call")
            cache.result_ids.add(payload.result.call_id)
            definition = next((tool.definition for tool in cache.available.tools
                if tool.definition.spec.name == payload.tool_name and payload.tool_name in cache.exchange_visible), None)
            is_error = payload.result.status != "success"
            if definition is None:
                content = (ModelContent("text", payload.result.content_json),)
            else:
                try:
                    content = tuple(ModelContent(part.kind, part.value) for part in tool_result_content(definition, payload.result))
                except InvalidInput:
                    # The original response stays in History; an invalid capability result is a Tool-view error.
                    content = (ModelContent("text", "Tool returned invalid structured content. Treat this result as unusable; request supported text/image output or use another tool."),)
                    is_error = True
            message = ModelMessage("tool", content, call_id=payload.result.call_id, is_error=is_error)
            RunRuntime._retain(cache, message)
            cache.exchange_messages.append(message)
            if payload.tool_name == "todo" and payload.result.status == "success":
                cache.todo = payload.result.content_json
            if payload.tool_name == "search_tools" and payload.result.status == "success":
                value = json.loads(payload.result.content_json)
                names = value.get("tools")
                if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                    raise InvalidInput("Tool exposure result has invalid names")
                cache.available = cache.available.expose(frozenset(names))
            if cache.result_ids == {call.call_id for call in cache.exchange.result.calls}:
                cache.additions.append(ContextUnit(entry.sequence,
                    tuple(cache.exchange_messages) + tuple(message for _, message in cache.deferred_inputs)))
                cache.exchange = None
                cache.exchange_visible = frozenset()
                cache.exchange_messages.clear()
                cache.deferred_inputs.clear()
                cache.result_ids.clear()
        elif isinstance(payload, ModelInputPayload):
            if cache.inflight is not None or cache.exchange is not None:
                raise InvalidHistory("Run History contains overlapping Model requests")
            RunRuntime._flush_inputs(cache, payload.read_through_sequence)
            cache.inflight = payload
            cache.available = cache.available.expose(frozenset(payload.visible_tool_names))

    async def _apply(self, changed: TransitionResult) -> None:
        views = changed.affected or (changed.run,)
        for view in views:
            if view.status in _TERMINAL:
                self.dispatcher.release(_key(view))
                self._pending.pop(view.id, None)
                self._attempts.pop(view.id, None)
            if view.status != "Running":
                self._caches.pop(view.id, None)
        for view in views:
            if view.id in changed.wake_run_ids and self.dispatcher.is_admitted(_key(view)):
                self.dispatcher.wake(_key(view))
        for view in views:
            if view.status in _TERMINAL:
                await self.dispatcher.wait_released(_key(view))
                self._pending.pop(view.id, None)
                self._attempts.pop(view.id, None)
                self._caches.pop(view.id, None)
                await self._cleanup(view)

    async def post_commit(self, changed: TransitionResult) -> None:
        """Apply scheduling and cleanup only after the caller's owner transaction committed."""
        await self._apply(changed)

    async def _cleanup(self, view: RunView) -> None:
        try:
            async with transaction(self._control) as tx:
                snapshot = await RunService(tx).read_snapshot(tenant_id=view.tenant_id, run_id=view.id)
            await self._model.release_continuation(tenant_id=view.tenant_id, run_id=view.id,
                model_id=snapshot.model.profile.model_id,
                terminal_status=cast(Literal["Completed", "Failed", "Cancelled", "Interrupted"], view.status))
        except Exception as error:  # noqa: BLE001 -- isolated post-terminal housekeeping cannot undo committed outcomes.
            # Housekeeping cannot reverse the committed terminal outcome or its capacity release.
            logger.warning("Run continuation cleanup failed: %s", type(error).__name__)

    async def _interrupt_all(self) -> None:
        while True:
            async with transaction(self._control) as tx:
                views = await RunService(tx).interrupt_batch(limit=1, consumer=self._consumer)
            if not views:
                return
            for view in views:
                self.dispatcher.release(_key(view))
                await self._cleanup(view)

    def _intake(self) -> None:
        if not self._accepting or self._closed:
            raise Conflict("Run Runtime is not accepting input")
