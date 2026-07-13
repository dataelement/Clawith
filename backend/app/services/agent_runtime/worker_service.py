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
from app.services.agent_runtime.cancel_source import DatabaseRuntimeCancelSource
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
from app.services.agent_runtime.graph import AgentRuntimeGraph, build_agent_runtime_graph
from app.services.agent_runtime.langgraph_driver import (
    LangGraphRuntimeDriver,
    RuntimeGraphRegistry,
    RuntimeInputSnapshotFactory,
)
from app.services.agent_runtime.model_step_service import RuntimeModelStepService
from app.services.agent_runtime.node_executor import DeterministicRuntimeNodeExecutor
from app.services.agent_runtime.projector import RuntimeProjector
from app.services.agent_runtime.run_compactor import RuntimeRunCompactorService
from app.services.agent_runtime.session_context_service import SessionContextService
from app.services.agent_runtime.session_context_compactor import LLMSessionContextCompactor
from app.services.agent_runtime.session_context_completion import (
    SessionContextCompletionHandler,
)
from app.services.agent_runtime.task_completion import TaskRuntimeCompletionHandler
from app.services.agent_runtime.tool_step_service import RuntimeToolStepService


logger = logging.getLogger(__name__)

_REQUIRED_PRODUCT_TABLES = (
    "agent_runs",
    "agent_run_commands",
    "agent_run_events",
    "agent_tool_executions",
    "session_context_states",
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
    graph_registry: RuntimeGraphRegistry
    driver: LangGraphRuntimeDriver
    worker: RuntimeCommandWorker


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
    context_builder = ContextBuilder(
        session_context_service,
        settings=runtime_settings,
    )
    cancel_source = DatabaseRuntimeCancelSource(session_factory=session_factory)
    model_service = RuntimeModelStepService(
        session_factory=session_factory,
        context_builder=context_builder,
    )
    tool_service = RuntimeToolStepService(
        session_factory=session_factory,
        cancel_source=cancel_source,
    )
    run_compactor = RuntimeRunCompactorService(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    node_executor = DeterministicRuntimeNodeExecutor(
        cancel_source=cancel_source,
        model_service=model_service,
        tool_service=tool_service,
        run_compactor=run_compactor,
    )
    graph = build_agent_runtime_graph(
        checkpointer=checkpointer,
        settings=runtime_settings,
    )
    graph_registry = RuntimeGraphRegistry([graph])
    driver = LangGraphRuntimeDriver(
        graph_registry=graph_registry,
        snapshot_factory=RuntimeInputSnapshotFactory(context_builder),
        node_executor=node_executor,
    )
    projector = RuntimeProjector(graph.compiled)
    session_context_compactor = LLMSessionContextCompactor(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    post_checkpoint_handler = RuntimeCheckpointSideEffects(
        session_factory=session_factory,
        projector=projector,
        terminal_handlers=(
            SessionContextCompletionHandler(
                session_factory=session_factory,
                context_service=session_context_service,
                compactor=session_context_compactor,
            ),
            TaskRuntimeCompletionHandler(session_factory=session_factory),
        ),
    )
    worker = RuntimeCommandWorker(
        session_factory=session_factory,
        lock_engine=lock_engine,
        checkpoint_reader=driver,
        command_executor=driver,
        post_checkpoint_handler=post_checkpoint_handler,
        claimant=claimant or runtime_worker_claimant(),
        settings=runtime_settings,
    )
    return RuntimeWorkerComponents(
        graph=graph,
        graph_registry=graph_registry,
        driver=driver,
        worker=worker,
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
    async with runtime_worker_context(
        settings=settings,
        checkpointer_manager=checkpointer_manager,
        session_factory=session_factory,
        lock_engine=lock_engine,
        claimant=claimant,
        verify_schema=verify_schema,
    ) as components:
        stop = asyncio.Event()
        daemon = RuntimeCommandDaemon(components.worker)
        task = asyncio.create_task(
            daemon.run(stop),
            name="agent-runtime-command-worker",
        )
        try:
            yield components
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


__all__ = [
    "RuntimeCommandDaemon",
    "RuntimeSchemaNotReady",
    "RuntimeWorkerComponents",
    "assert_runtime_schema_ready",
    "build_runtime_worker_components",
    "running_runtime_worker_context",
    "runtime_worker_claimant",
    "runtime_worker_context",
]
