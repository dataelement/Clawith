"""Runtime worker composition and daemon lifecycle tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections import deque
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from app.config import Settings
from app.services.agent_runtime.command_worker import CommandWorkResult, RuntimeRunRecord
from app.services.agent_runtime.heartbeat_completion import (
    HeartbeatRuntimeCompletionHandler,
)
from app.services.agent_runtime.session_context_completion import (
    SessionContextCompletionHandler,
)
from app.services.agent_runtime.state import RunRegistrySnapshot
from app.services.agent_runtime.task_completion import TaskRuntimeCompletionHandler
from app.services.agent_runtime.trigger_completion import TriggerRuntimeCompletionHandler
from app.services.agent_runtime.worker_service import (
    RuntimeCommandDaemon,
    RuntimeSchemaNotReady,
    assert_runtime_schema_ready,
    build_runtime_worker_components,
    running_runtime_worker_context,
    runtime_worker_context,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_GRAPH_NAME="worker_service_test",
        AGENT_RUNTIME_GRAPH_VERSION="v1",
    )


class _Worker:
    def __init__(self, stop: asyncio.Event, *results: object) -> None:
        self.stop = stop
        self.results = deque(results)
        self.calls = 0

    async def run_once(self) -> CommandWorkResult:
        self.calls += 1
        result = self.results.popleft()
        if not self.results:
            self.stop.set()
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _SessionFactory:
    def __call__(self) -> _Session:
        return _Session()


class _Engine:
    pass


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _SchemaConnection:
    def __init__(self, tables: set[str]) -> None:
        self.tables = tables

    async def __aenter__(self) -> "_SchemaConnection":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False

    async def execute(self, _statement, parameters) -> _ScalarResult:
        name = parameters["table_name"]
        return _ScalarResult(name if name in self.tables else None)


class _SchemaEngine:
    def __init__(self, tables: set[str]) -> None:
        self.connection = _SchemaConnection(tables)

    def connect(self) -> _SchemaConnection:
        return self.connection


@pytest.mark.asyncio
async def test_daemon_continues_after_iteration_error_until_stopped() -> None:
    stop = asyncio.Event()
    worker = _Worker(
        stop,
        RuntimeError("database unavailable"),
        CommandWorkResult(status="idle"),
    )
    daemon = RuntimeCommandDaemon(
        worker,  # type: ignore[arg-type]
        idle_delay_seconds=0.001,
        retry_delay_seconds=0.001,
        error_delay_seconds=0.001,
    )

    await asyncio.wait_for(daemon.run(stop), timeout=1)

    assert worker.calls == 2


def test_component_builder_installs_one_pinned_graph_and_shared_driver() -> None:
    components = build_runtime_worker_components(
        checkpointer=InMemorySaver(),
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        lock_engine=_Engine(),  # type: ignore[arg-type]
        claimant="worker-test",
        settings=_settings(),
    )

    assert components.graph.identity.name == "worker_service_test"
    assert components.graph.identity.version == "v1"
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="test",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="worker_service_test",
        graph_version="v1",
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        registry=registry,
    )
    assert components.graph_registry.resolve(run) is components.graph
    assert components.worker._checkpoint_reader is components.driver
    assert components.worker._command_executor is components.driver
    terminal_handlers = components.worker._post_checkpoint_handler._terminal_handlers
    assert [type(handler) for handler in terminal_handlers] == [
        SessionContextCompletionHandler,
        TaskRuntimeCompletionHandler,
        TriggerRuntimeCompletionHandler,
        HeartbeatRuntimeCompletionHandler,
    ]


@pytest.mark.asyncio
async def test_worker_context_keeps_supplied_checkpointer_open() -> None:
    timeline: list[str] = []

    @asynccontextmanager
    async def manager():
        timeline.append("checkpointer_enter")
        yield InMemorySaver()
        timeline.append("checkpointer_exit")

    async with runtime_worker_context(
        settings=_settings(),
        checkpointer_manager=manager(),
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        lock_engine=_Engine(),  # type: ignore[arg-type]
        claimant="worker-test",
        verify_schema=False,
    ):
        timeline.append("worker_active")

    assert timeline == [
        "checkpointer_enter",
        "worker_active",
        "checkpointer_exit",
    ]


@pytest.mark.asyncio
async def test_running_context_stops_daemon_before_closing_checkpointer() -> None:
    timeline: list[str] = []

    @asynccontextmanager
    async def manager():
        timeline.append("checkpointer_enter")
        yield InMemorySaver()
        timeline.append("checkpointer_exit")

    async with running_runtime_worker_context(
        settings=_settings(),
        checkpointer_manager=manager(),
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        lock_engine=_Engine(),  # type: ignore[arg-type]
        claimant="worker-test",
        verify_schema=False,
    ):
        timeline.append("daemon_active")
        await asyncio.sleep(0)

    assert timeline == [
        "checkpointer_enter",
        "daemon_active",
        "checkpointer_exit",
    ]


@pytest.mark.asyncio
async def test_schema_readiness_requires_every_product_table() -> None:
    with (
        patch(
            "app.services.agent_runtime.worker_service._checkpoint_migration_version",
            new=AsyncMock(return_value=9),
        ),
        pytest.raises(RuntimeSchemaNotReady, match="agent_tool_executions") as raised,
    ):
        await assert_runtime_schema_ready(
            _SchemaEngine(
                {
                    "agent_runs",
                    "agent_run_commands",
                    "agent_run_events",
                    "session_context_states",
                }
            ),  # type: ignore[arg-type]
            settings=_settings(),
        )

    assert raised.value.code == "product_schema_incomplete"


@pytest.mark.asyncio
async def test_schema_readiness_requires_pinned_checkpoint_version() -> None:
    with (
        patch(
            "app.services.agent_runtime.worker_service._checkpoint_migration_version",
            new=AsyncMock(return_value=8),
        ),
        pytest.raises(RuntimeSchemaNotReady, match="expected 9") as raised,
    ):
        await assert_runtime_schema_ready(
            _SchemaEngine(
                {
                    "agent_runs",
                    "agent_run_commands",
                    "agent_run_events",
                    "agent_tool_executions",
                    "session_context_states",
                }
            ),  # type: ignore[arg-type]
            settings=_settings(),
        )

    assert raised.value.code == "checkpoint_schema_outdated"


@pytest.mark.asyncio
async def test_schema_readiness_accepts_complete_pinned_schema() -> None:
    checkpoint_version = AsyncMock(return_value=9)
    with patch(
        "app.services.agent_runtime.worker_service._checkpoint_migration_version",
        new=checkpoint_version,
    ):
        await assert_runtime_schema_ready(
            _SchemaEngine(
                {
                    "agent_runs",
                    "agent_run_commands",
                    "agent_run_events",
                    "agent_tool_executions",
                    "session_context_states",
                }
            ),  # type: ignore[arg-type]
            settings=_settings(),
        )

    checkpoint_version.assert_awaited_once()
