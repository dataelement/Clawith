"""Production composition and daemon loop for the durable Runtime worker."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
import asyncio
import logging
import os
import socket
from typing import AsyncIterator
import uuid

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection as PsycopgAsyncConnection
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.services.agent_runtime.a2a_completion import A2ARuntimeCompletionHandler
from app.services.agent_runtime.a2a_runtime import RuntimeA2AService
from app.services.agent_runtime.async_tool_poll import (
    AsyncToolPollResult,
    AsyncToolPollScheduler,
)
from app.services.agent_runtime.cancel_source import DatabaseRuntimeCancelSource
from app.services.agent_runtime.channel_delivery import (
    ChannelDeliveryWorkResult,
    ChannelDeliveryWorker,
)
from app.services.agent_runtime.channel_provider_delivery import (
    DatabaseChannelDeliverySender,
)
from app.services.agent_runtime.checkpoint_side_effects import RuntimeCheckpointSideEffects
from app.services.agent_runtime.checkpointer import (
    checkpoint_database_url,
    create_checkpointer,
)
from app.services.agent_runtime.command_worker import (
    CommandWorkResult,
    RuntimeCommandWorker,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.context_builder import ContextBuilder
from app.services.agent_runtime.graph import (
    AgentRuntimeGraph,
    RuntimeGraphIdentity,
    build_agent_runtime_graph,
)
from app.services.agent_runtime.group_acknowledgement import (
    RuntimeGroupStartAcknowledgementHandler,
)
from app.services.agent_runtime.heartbeat_completion import (
    HeartbeatRuntimeCompletionHandler,
)
from app.services.agent_runtime.langgraph_driver import (
    LangGraphRuntimeDriver,
    RuntimeGraphRegistry,
    RuntimeInputSnapshotFactory,
)
from app.services.agent_runtime.model_step_service import RuntimeModelStepService
from app.services.agent_runtime.node_executor import DeterministicRuntimeNodeExecutor
from app.services.agent_runtime.onboarding_completion import (
    OnboardingRuntimeCompletionHandler,
)
from app.services.agent_runtime.planning import (
    PlanningModelService,
    PlanningRuntimeNodeExecutor,
    RuntimeNodeExecutorRouter,
)
from app.services.agent_runtime.planning_scheduler import PlanningCheckpointScheduler
from app.services.agent_runtime.persistence import release_rejected_start_lanes
from app.services.agent_runtime.product_reconciler import (
    ProductReconcileResult,
    RuntimeProductReconciler,
)
from app.services.agent_runtime.run_compactor import RuntimeRunCompactorService
from app.services.agent_runtime.scheduling_lane import SchedulingLaneCompletionHandler
from app.services.agent_runtime.session_context_service import SessionContextService
from app.services.agent_runtime.session_context_compactor import LLMSessionContextCompactor
from app.services.agent_runtime.session_context_background import (
    SessionCompactPolicyResolver,
    SessionContextCompactionScanner,
    SessionContextMessageCompactionService,
)
from app.services.agent_runtime.session_context_completion import (
    SessionContextCompletionHandler,
)
from app.services.agent_runtime.task_completion import TaskRuntimeCompletionHandler
from app.services.agent_runtime.tool_step_service import RuntimeToolStepService
from app.services.agent_runtime.tool_result_store import (
    ToolResultReconcileResult,
    ToolResultReconciler,
    ToolResultStore,
)
from app.services.agent_runtime.trigger_completion import TriggerRuntimeCompletionHandler
from app.services.agent_runtime.verification import (
    RuntimeToolReferenceReader,
    ToolLedgerRuntimeVerifier,
)


logger = logging.getLogger(__name__)

_REQUIRED_PRODUCT_TABLES = (
    "agent_runs",
    "agent_run_commands",
    "agent_run_events",
    "agent_tool_executions",
    "session_context_states",
    "channel_deliveries",
)
_EXPECTED_CHECKPOINT_MIGRATION = len(AsyncPostgresSaver.MIGRATIONS) - 1


class RuntimeSchemaNotReady(RuntimeError):
    """Runtime code is enabled before its explicit migrations are complete."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeWorkerComponents:
    """Long-lived Runtime objects sharing one installed Checkpointer."""

    graph: AgentRuntimeGraph
    planning_graph: AgentRuntimeGraph
    graph_registry: RuntimeGraphRegistry
    driver: LangGraphRuntimeDriver
    worker: RuntimeCommandWorker
    async_tool_poll_scheduler: AsyncToolPollScheduler
    tool_result_reconciler: ToolResultReconciler
    product_reconciler: RuntimeProductReconciler
    channel_delivery_worker: ChannelDeliveryWorker
    session_context_scanner: SessionContextCompactionScanner


def runtime_worker_claimant() -> str:
    """Return a process-unique claimant that fits the persisted column."""
    hostname = socket.gethostname().strip() or "unknown-host"
    return f"{hostname}:{os.getpid()}:{uuid.uuid4().hex}"[:128]


async def _checkpoint_migration_version(settings: Settings) -> int | None:
    try:
        connection = await PsycopgAsyncConnection.connect(
            checkpoint_database_url(settings),
            autocommit=True,
        )
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT max(v) FROM checkpoint_migrations")
                row = await cursor.fetchone()
    except Exception as exc:
        raise RuntimeSchemaNotReady(
            "checkpoint_schema_unavailable",
            "LangGraph checkpoint schema is unavailable; run the explicit setup command",
        ) from exc
    if row is None or row[0] is None:
        return None
    return int(row[0])


async def assert_runtime_schema_ready(
    engine: AsyncEngine,
    *,
    settings: Settings | None = None,
) -> None:
    """Fail startup unless product Alembic and official saver setup both ran."""
    runtime_settings = settings or get_settings()
    missing: list[str] = []
    try:
        async with engine.connect() as connection:
            for table_name in _REQUIRED_PRODUCT_TABLES:
                result = await connection.execute(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": table_name},
                )
                if result.scalar_one_or_none() is None:
                    missing.append(table_name)
    except Exception as exc:
        raise RuntimeSchemaNotReady(
            "product_schema_unavailable",
            "Agent Runtime product schema could not be inspected",
        ) from exc
    if missing:
        raise RuntimeSchemaNotReady(
            "product_schema_incomplete",
            "Agent Runtime migration is required; missing tables: " + ", ".join(missing),
        )

    checkpoint_version = await _checkpoint_migration_version(runtime_settings)
    if checkpoint_version != _EXPECTED_CHECKPOINT_MIGRATION:
        raise RuntimeSchemaNotReady(
            "checkpoint_schema_outdated",
            "LangGraph checkpoint setup version does not match the pinned package "
            f"(expected {_EXPECTED_CHECKPOINT_MIGRATION}, found {checkpoint_version})",
        )


def build_runtime_worker_components(
    *,
    checkpointer: BaseCheckpointSaver,
    session_factory: RuntimeSessionFactory,
    lock_engine: AsyncEngine,
    claimant: str | None = None,
    settings: Settings | None = None,
) -> RuntimeWorkerComponents:
    """Compose one Graph and Worker without opening connections or starting tasks."""
    runtime_settings = settings or get_settings()
    session_context_service = SessionContextService(settings=runtime_settings)
    session_context_compactor = LLMSessionContextCompactor(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    context_builder = ContextBuilder(
        session_context_service,
        settings=runtime_settings,
        session_context_compactor=session_context_compactor,
    )
    cancel_source = DatabaseRuntimeCancelSource(session_factory=session_factory)
    model_service = RuntimeModelStepService(
        session_factory=session_factory,
        context_builder=context_builder,
    )
    tool_result_store = ToolResultStore(session_factory=session_factory)
    tool_result_reconciler = ToolResultReconciler(
        session_factory=session_factory,
        result_store=tool_result_store,
    )
    reference_reader = RuntimeToolReferenceReader(
        session_factory=session_factory,
    )
    tool_service = RuntimeToolStepService(
        session_factory=session_factory,
        cancel_source=cancel_source,
        a2a_service=RuntimeA2AService(
            session_factory=session_factory,
            settings=runtime_settings,
        ),
        tool_result_store=tool_result_store,
        tool_result_reconciler=tool_result_reconciler,
    )
    run_compactor = RuntimeRunCompactorService(
        settings=runtime_settings,
        input_loader=model_service.compact_inputs,
    )
    agent_node_executor = DeterministicRuntimeNodeExecutor(
        cancel_source=cancel_source,
        model_service=model_service,
        tool_service=tool_service,
        run_compactor=run_compactor,
        verifier=ToolLedgerRuntimeVerifier(
            session_factory=session_factory,
            result_store=tool_result_store,
            reference_exists=reference_reader.reference_exists,
        ),
    )
    graph = build_agent_runtime_graph(
        checkpointer=checkpointer,
        settings=runtime_settings,
    )
    planning_graph = build_agent_runtime_graph(
        checkpointer=checkpointer,
        settings=runtime_settings,
        identity=RuntimeGraphIdentity.planning_from_settings(runtime_settings),
    )
    planning_node_executor = PlanningRuntimeNodeExecutor(
        cancel_source=cancel_source,
        model_service=PlanningModelService(session_factory=session_factory),
    )
    node_executor = RuntimeNodeExecutorRouter(
        agent_executor=agent_node_executor,
        planning_executor=planning_node_executor,
    )
    graph_registry = RuntimeGraphRegistry([graph, planning_graph])
    driver = LangGraphRuntimeDriver(
        graph_registry=graph_registry,
        snapshot_factory=RuntimeInputSnapshotFactory(context_builder),
        node_executor=node_executor,
    )
    session_context_scanner = SessionContextCompactionScanner(
        session_factory=session_factory,
        service=SessionContextMessageCompactionService(
            lock_engine=lock_engine,
            compactor=session_context_compactor,
            context_service=session_context_service,
            policy_resolver=SessionCompactPolicyResolver(
                settings=runtime_settings,
            ),
        ),
        settings=runtime_settings,
    )
    post_checkpoint_handler = RuntimeCheckpointSideEffects(
        session_factory=session_factory,
        checkpoint_handlers=(
            PlanningCheckpointScheduler(
                session_factory=session_factory,
                settings=runtime_settings,
            ),
        ),
        terminal_handlers=(
            SessionContextCompletionHandler(
                session_factory=session_factory,
                context_service=session_context_service,
            ),
            TaskRuntimeCompletionHandler(session_factory=session_factory),
            TriggerRuntimeCompletionHandler(session_factory=session_factory),
            HeartbeatRuntimeCompletionHandler(session_factory=session_factory),
            OnboardingRuntimeCompletionHandler(session_factory=session_factory),
            A2ARuntimeCompletionHandler(session_factory=session_factory),
            SchedulingLaneCompletionHandler(session_factory=session_factory),
        ),
    )
    resolved_claimant = claimant or runtime_worker_claimant()
    worker = RuntimeCommandWorker(
        session_factory=session_factory,
        lock_engine=lock_engine,
        checkpoint_reader=driver,
        command_executor=driver,
        pre_command_handler=RuntimeGroupStartAcknowledgementHandler(
            session_factory=session_factory,
        ),
        post_checkpoint_handler=post_checkpoint_handler,
        claimant=resolved_claimant,
        settings=runtime_settings,
    )
    async_tool_poll_scheduler = AsyncToolPollScheduler(
        session_factory=session_factory,
    )
    product_reconciler = RuntimeProductReconciler(
        session_factory=session_factory,
        checkpoint_reader=driver,
        handler=post_checkpoint_handler,
    )
    channel_delivery_worker = ChannelDeliveryWorker(
        session_factory=session_factory,
        sender=DatabaseChannelDeliverySender(session_factory=session_factory),
        claimant=resolved_claimant,
        settings=runtime_settings,
    )
    return RuntimeWorkerComponents(
        graph=graph,
        planning_graph=planning_graph,
        graph_registry=graph_registry,
        driver=driver,
        worker=worker,
        async_tool_poll_scheduler=async_tool_poll_scheduler,
        tool_result_reconciler=tool_result_reconciler,
        product_reconciler=product_reconciler,
        channel_delivery_worker=channel_delivery_worker,
        session_context_scanner=session_context_scanner,
    )


class RuntimeCommandDaemon:
    """Continuously drain the Command Inbox with bounded idle/error polling."""

    def __init__(
        self,
        worker: RuntimeCommandWorker,
        *,
        idle_delay_seconds: float = 0.25,
        retry_delay_seconds: float = 0.1,
        error_delay_seconds: float = 1.0,
    ) -> None:
        delays = (idle_delay_seconds, retry_delay_seconds, error_delay_seconds)
        if any(delay <= 0 for delay in delays):
            raise ValueError("Runtime daemon delays must be positive")
        self._worker = worker
        self._idle_delay_seconds = idle_delay_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._error_delay_seconds = error_delay_seconds

    @staticmethod
    async def _wait(stop: asyncio.Event, delay: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stopped; individual command failures never kill the daemon."""
        while not stop.is_set():
            delay = 0.0
            try:
                result = await self._worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime Command Worker iteration failed")
                delay = self._error_delay_seconds
            else:
                delay = self._delay_after(result)
            if delay:
                await self._wait(stop, delay)

    def _delay_after(self, result: CommandWorkResult) -> float:
        if result.status == "idle":
            return self._idle_delay_seconds
        if result.status == "retry":
            return self._retry_delay_seconds
        return 0.0


class ChannelDeliveryDaemon:
    """Continuously drain provider deliveries independently of Graph execution."""

    def __init__(
        self,
        worker: ChannelDeliveryWorker,
        *,
        scan_delay_seconds: float,
        error_delay_seconds: float = 1.0,
    ) -> None:
        if scan_delay_seconds <= 0 or error_delay_seconds <= 0:
            raise ValueError("Channel delivery daemon delays must be positive")
        self._worker = worker
        self._scan_delay_seconds = scan_delay_seconds
        self._error_delay_seconds = error_delay_seconds

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime channel delivery iteration failed")
                delay = self._error_delay_seconds
            else:
                delay = self._delay_after(result)
            if delay:
                await RuntimeCommandDaemon._wait(stop, delay)

    def _delay_after(self, result: ChannelDeliveryWorkResult) -> float:
        if result.status in {"idle", "retry", "failed"}:
            return self._scan_delay_seconds
        return 0.0


class AsyncToolPollDaemon:
    """Schedule timer resumes without executing Tools outside LangGraph."""

    def __init__(
        self,
        scheduler: AsyncToolPollScheduler,
        *,
        scan_delay_seconds: float,
        error_delay_seconds: float = 1.0,
    ) -> None:
        if scan_delay_seconds <= 0 or error_delay_seconds <= 0:
            raise ValueError("async Tool poll daemon delays must be positive")
        self._scheduler = scheduler
        self._scan_delay_seconds = scan_delay_seconds
        self._error_delay_seconds = error_delay_seconds

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._scheduler.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime async Tool poll scheduling iteration failed")
                delay = self._error_delay_seconds
            else:
                delay = self._delay_after(result)
            if delay:
                await RuntimeCommandDaemon._wait(stop, delay)

    def _delay_after(self, result: AsyncToolPollResult) -> float:
        if result.status in {"idle", "deferred"}:
            return self._scan_delay_seconds
        return 0.0


class ProductReconcileDaemon:
    """Retry products independently from Command and Graph execution."""

    def __init__(
        self,
        reconciler: RuntimeProductReconciler,
        *,
        scan_delay_seconds: float = 0.5,
        error_delay_seconds: float = 1.0,
    ) -> None:
        if scan_delay_seconds <= 0 or error_delay_seconds <= 0:
            raise ValueError("product reconciliation daemon delays must be positive")
        self._reconciler = reconciler
        self._scan_delay_seconds = scan_delay_seconds
        self._error_delay_seconds = error_delay_seconds

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._reconciler.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime product reconciliation iteration failed")
                delay = self._error_delay_seconds
            else:
                delay = self._delay_after(result)
            if delay:
                await RuntimeCommandDaemon._wait(stop, delay)

    def _delay_after(self, result: ProductReconcileResult) -> float:
        if result.status in {"idle", "retry"}:
            return self._scan_delay_seconds
        return 0.0


class ToolResultReconcileDaemon:
    """Recover archived results independently without re-executing tools."""

    def __init__(
        self,
        reconciler: ToolResultReconciler,
        *,
        scan_delay_seconds: float = 0.5,
        error_delay_seconds: float = 1.0,
    ) -> None:
        if scan_delay_seconds <= 0 or error_delay_seconds <= 0:
            raise ValueError("tool result reconciliation daemon delays must be positive")
        self._reconciler = reconciler
        self._scan_delay_seconds = scan_delay_seconds
        self._error_delay_seconds = error_delay_seconds

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._reconciler.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime tool result reconciliation iteration failed")
                delay = self._error_delay_seconds
            else:
                delay = self._delay_after(result)
            if delay:
                await RuntimeCommandDaemon._wait(stop, delay)

    def _delay_after(self, result: ToolResultReconcileResult) -> float:
        if result.status in {"idle", "deferred"}:
            return self._scan_delay_seconds
        return 0.0


@asynccontextmanager
async def runtime_worker_context(
    *,
    settings: Settings | None = None,
    checkpointer_manager: AbstractAsyncContextManager[BaseCheckpointSaver] | None = None,
    session_factory: RuntimeSessionFactory | None = None,
    lock_engine: AsyncEngine | None = None,
    claimant: str | None = None,
    verify_schema: bool = True,
) -> AsyncIterator[RuntimeWorkerComponents]:
    """Keep the Checkpointer open for exactly the Worker component lifetime."""
    runtime_settings = settings or get_settings()
    if session_factory is None or lock_engine is None:
        from app.database import async_session, engine

        session_factory = session_factory or async_session
        lock_engine = lock_engine or engine
    if verify_schema:
        await assert_runtime_schema_ready(lock_engine, settings=runtime_settings)
    async with session_factory() as db:
        async with db.begin():
            repaired_lanes = await release_rejected_start_lanes(db)
    if repaired_lanes:
        logger.warning(
            "Released scheduling lanes abandoned by rejected start commands",
            extra={"repaired_lane_count": repaired_lanes},
        )
    manager = checkpointer_manager or create_checkpointer(runtime_settings)
    async with manager as checkpointer:
        yield build_runtime_worker_components(
            checkpointer=checkpointer,
            session_factory=session_factory,
            lock_engine=lock_engine,
            claimant=claimant,
            settings=runtime_settings,
        )


@asynccontextmanager
async def running_runtime_worker_context(
    *,
    settings: Settings | None = None,
    checkpointer_manager: AbstractAsyncContextManager[BaseCheckpointSaver] | None = None,
    session_factory: RuntimeSessionFactory | None = None,
    lock_engine: AsyncEngine | None = None,
    claimant: str | None = None,
    verify_schema: bool = True,
) -> AsyncIterator[RuntimeWorkerComponents]:
    """Run and cancel the daemon within the Checkpointer component lifetime."""
    runtime_settings = settings or get_settings()
    async with runtime_worker_context(
        settings=runtime_settings,
        checkpointer_manager=checkpointer_manager,
        session_factory=session_factory,
        lock_engine=lock_engine,
        claimant=claimant,
        verify_schema=verify_schema,
    ) as components:
        stop = asyncio.Event()
        command_tasks = [
            asyncio.create_task(
                RuntimeCommandDaemon(components.worker).run(stop),
                name=f"agent-runtime-command-worker-{slot + 1}",
            )
            for slot in range(runtime_settings.AGENT_RUNTIME_COMMAND_CONCURRENCY)
        ]
        compact_task = asyncio.create_task(
            components.session_context_scanner.run(stop),
            name="agent-runtime-session-context-compact",
        )
        channel_delivery_task = asyncio.create_task(
            ChannelDeliveryDaemon(
                components.channel_delivery_worker,
                scan_delay_seconds=(
                    runtime_settings.AGENT_RUNTIME_CHANNEL_DELIVERY_SCAN_SECONDS
                ),
            ).run(stop),
            name="agent-runtime-channel-delivery",
        )
        async_tool_poll_task = asyncio.create_task(
            AsyncToolPollDaemon(
                components.async_tool_poll_scheduler,
                scan_delay_seconds=(
                    runtime_settings.AGENT_RUNTIME_ASYNC_TOOL_POLL_SCAN_SECONDS
                ),
            ).run(stop),
            name="agent-runtime-async-tool-poll",
        )
        product_reconcile_task = asyncio.create_task(
            ProductReconcileDaemon(components.product_reconciler).run(stop),
            name="agent-runtime-product-reconcile",
        )
        tool_result_reconcile_task = asyncio.create_task(
            ToolResultReconcileDaemon(components.tool_result_reconciler).run(stop),
            name="agent-runtime-tool-result-reconcile",
        )
        try:
            yield components
        finally:
            stop.set()
            for task in command_tasks:
                task.cancel()
            compact_task.cancel()
            channel_delivery_task.cancel()
            async_tool_poll_task.cancel()
            product_reconcile_task.cancel()
            tool_result_reconcile_task.cancel()
            for task in command_tasks:
                with suppress(asyncio.CancelledError):
                    await task
            with suppress(asyncio.CancelledError):
                await compact_task
            with suppress(asyncio.CancelledError):
                await channel_delivery_task
            with suppress(asyncio.CancelledError):
                await async_tool_poll_task
            with suppress(asyncio.CancelledError):
                await product_reconcile_task
            with suppress(asyncio.CancelledError):
                await tool_result_reconcile_task


__all__ = [
    "ChannelDeliveryDaemon",
    "AsyncToolPollDaemon",
    "ProductReconcileDaemon",
    "ToolResultReconcileDaemon",
    "RuntimeCommandDaemon",
    "RuntimeSchemaNotReady",
    "RuntimeWorkerComponents",
    "assert_runtime_schema_ready",
    "build_runtime_worker_components",
    "running_runtime_worker_context",
    "runtime_worker_claimant",
    "runtime_worker_context",
]
