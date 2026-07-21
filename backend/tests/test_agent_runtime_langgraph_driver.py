"""Concrete LangGraph command driver tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncConnection
import pytest

from app.config import Settings
from app.services.agent_runtime.command_worker import (
    CommandExecutionRejected,
    RuntimeCommandRecord,
    RuntimeCommandType,
    RuntimeRunRecord,
)
from app.services.agent_runtime.graph import build_agent_runtime_graph
from app.services.agent_runtime.langgraph_driver import (
    LangGraphRuntimeDriver,
    RuntimeGraphRegistry,
    RuntimeInputSnapshotFactory,
    StaticRuntimeInputSnapshotFactory,
)
from app.services.agent_runtime.state import (
    JsonValue,
    RunInputSnapshots,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeName,
    RuntimeNodeExecutor,
    RuntimeStateUpdate,
    runtime_messages_as_json,
)


def _settings(
    *,
    graph_name: str = "driver_graph",
    graph_version: str = "v1",
) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_GRAPH_NAME=graph_name,
        AGENT_RUNTIME_GRAPH_VERSION=graph_version,
    )


class CompletingExecutor:
    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context, resume_value
        if node == "compact":
            return {"lifecycle": {"status": "running", "next_route": "model"}}
        if node == "model":
            return {"lifecycle": {"status": "verifying", "next_route": "verify"}}
        if node == "verify":
            return {
                "lifecycle": {
                    "status": "completed",
                    "next_route": "terminal",
                    "final_answer": "done",
                }
            }
        return {"lifecycle": dict(state["lifecycle"])}


class ContextCapturingExecutor(CompletingExecutor):
    def __init__(self) -> None:
        self.model_turn_limits: list[int | None] = []

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        self.model_turn_limits.append(context.model_turn_limit)
        return await super().execute(
            node,
            state,
            context,
            resume_value=resume_value,
        )


class SummaryCompletingExecutor(CompletingExecutor):
    def __init__(self) -> None:
        self._summary_written = False

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        if node == "compact" and not self._summary_written:
            self._summary_written = True
            return {
                "thread_summary": {
                    "task_goal_and_constraints": "preserve across Runs",
                    "completed_work_and_results": "",
                    "key_decisions_and_evidence": "",
                    "unfinished_or_blocked": "",
                    "next_actions": "continue",
                },
                "summary_covered_through_message_id": "summary-boundary",
                "lifecycle": {"status": "running", "next_route": "model"},
            }
        return await super().execute(
            node,
            state,
            context,
            resume_value=resume_value,
        )


class WaitingExecutor:
    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context
        if node == "compact":
            return {"lifecycle": {"status": "running", "next_route": "model"}}
        if node == "model":
            return {
                "lifecycle": {
                    "status": "waiting_user",
                    "next_route": "wait",
                    "waiting_request": {
                        "waiting_type": "user",
                        "reason": "confirm",
                        "correlation_id": "correlation-1",
                    },
                }
            }
        if node == "wait":
            return {
                "lifecycle": {
                    "status": "completed",
                    "next_route": "terminal",
                    "waiting_request": None,
                    "final_answer": str(resume_value),
                }
            }
        return {"lifecycle": dict(state["lifecycle"])}


def _snapshots(
    *,
    initial_input: dict[str, JsonValue] | None = None,
) -> RunInputSnapshots:
    return RunInputSnapshots(
        session_context={"version": 0, "summary": ""},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input=initial_input or {"message": "hello"},
    )


def _run(run_id: uuid.UUID) -> RuntimeRunRecord:
    tenant_id = uuid.uuid4()
    return RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        goal="Answer the user",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="driver_graph",
        graph_version="v1",
        agent_id=str(uuid.uuid4()),
    )


def _command(
    run: RuntimeRunRecord,
    command_type: str,
    *,
    payload: dict[str, JsonValue] | None = None,
) -> RuntimeCommandRecord:
    return RuntimeCommandRecord(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        command_type=cast(RuntimeCommandType, command_type),
        payload=payload or {},
        actor_user_id=uuid.uuid4(),
        actor_agent_id=None,
    )


def _driver(executor: object) -> LangGraphRuntimeDriver:
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    return LangGraphRuntimeDriver(
        graph_registry=RuntimeGraphRegistry([graph]),
        snapshot_factory=StaticRuntimeInputSnapshotFactory(_snapshots()),
        node_executor=cast(RuntimeNodeExecutor, executor),
    )


def _connection() -> AsyncConnection:
    return cast(AsyncConnection, object())


class _FakeAsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _CapturingContextBuilder:
    def __init__(self) -> None:
        self.initial_input: dict[str, JsonValue] | None = None
        self.kwargs = None

    async def capture_run_inputs(self, _db, **kwargs) -> RunInputSnapshots:
        self.kwargs = kwargs
        self.initial_input = kwargs["initial_input"]
        return _snapshots()


@pytest.mark.asyncio
async def test_snapshot_factory_keeps_runtime_metadata_out_of_model_input(monkeypatch) -> None:
    run = _run(uuid.uuid4())
    command = _command(
        run,
        "start",
        payload={
            "message": "hello",
            "__clawith_runtime": {"requested_model_turn_limit": 12},
        },
    )
    builder = _CapturingContextBuilder()
    monkeypatch.setattr(
        "app.services.agent_runtime.langgraph_driver.AsyncSession",
        lambda **_kwargs: _FakeAsyncSession(),
    )

    await RuntimeInputSnapshotFactory(cast(object, builder)).capture(  # type: ignore[arg-type]
        connection=_connection(),
        run=run,
        command=command,
    )

    assert builder.initial_input == {"message": "hello"}


@pytest.mark.asyncio
async def test_snapshot_factory_passes_immutable_source_and_scheduling_position(monkeypatch) -> None:
    message_id = uuid.uuid4()
    created_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    run = replace(
        _run(uuid.uuid4()),
        source_id=str(message_id),
        scheduling_position_created_at=created_at,
        scheduling_position_id=message_id,
    )
    command = _command(run, "start", payload={"message_id": str(message_id)})
    builder = _CapturingContextBuilder()
    monkeypatch.setattr(
        "app.services.agent_runtime.langgraph_driver.AsyncSession",
        lambda **_kwargs: _FakeAsyncSession(),
    )

    await RuntimeInputSnapshotFactory(cast(object, builder)).capture(  # type: ignore[arg-type]
        connection=_connection(),
        run=run,
        command=command,
    )

    assert builder.kwargs is not None
    assert builder.kwargs["source_type"] == "chat"
    assert builder.kwargs["source_id"] == str(message_id)
    assert builder.kwargs["scheduling_position_created_at"] == created_at
    assert builder.kwargs["scheduling_position_id"] == message_id


@pytest.mark.asyncio
async def test_driver_injects_the_run_frozen_model_turn_limit() -> None:
    run = replace(_run(uuid.uuid4()), model_turn_limit=17)
    command = _command(run, "start")
    executor = ContextCapturingExecutor()
    driver = _driver(executor)

    await driver.execute(
        connection=_connection(),
        run=run,
        command=command,
        checkpoint=None,
    )

    assert executor.model_turn_limits
    assert set(executor.model_turn_limits) == {17}


@pytest.mark.asyncio
async def test_start_checkpoints_carry_namespaced_command_metadata_without_registry_mirror() -> None:
    run = _run(uuid.uuid4())
    command = _command(run, "start", payload={"message": "hello"})
    driver = _driver(CompletingExecutor())

    assert await driver.read_latest(connection=_connection(), run=run) is None
    await driver.execute(
        connection=_connection(),
        run=run,
        command=command,
        checkpoint=None,
    )
    observed = await driver.read_latest(connection=_connection(), run=run)

    assert observed is not None
    assert "registry" not in observed.state
    assert observed.state["lifecycle"]["status"] == "completed"
    messages = runtime_messages_as_json(observed.state)
    assert messages[-1]["content"] == "hello"
    assert messages[-1]["runtime_input"] == "current"
    assert messages[-1]["runtime_run_id"] == str(run.run_id)
    assert observed.metadata["clawith_run_id"] == str(run.run_id)
    assert observed.metadata["clawith_command_id"] == str(command.id)
    assert observed.next_nodes == ()
    assert observed.tasks == ()
    assert observed.interrupts == ()
    assert "last_applied_command_ids" not in observed.state["lifecycle"]


@pytest.mark.asyncio
async def test_two_direct_runs_append_to_one_native_thread() -> None:
    thread_id = str(uuid.uuid4())
    first = replace(_run(uuid.uuid4()), thread_id=thread_id)
    second_base = _run(uuid.uuid4())
    second = replace(
        second_base,
        tenant_id=first.tenant_id,
        thread_id=thread_id,
    )
    driver = _driver(CompletingExecutor())

    await driver.execute(
        connection=_connection(),
        run=first,
        command=_command(first, "start"),
        checkpoint=None,
    )
    await driver.execute(
        connection=_connection(),
        run=second,
        command=_command(second, "start"),
        checkpoint=None,
    )

    observed = await driver.read_latest(connection=_connection(), run=second)
    assert observed is not None
    messages = runtime_messages_as_json(observed.state)
    assert [message["content"] for message in messages] == ["hello", "hello"]
    assert len({message["id"] for message in messages}) == 2
    assert [message["runtime_run_id"] for message in messages] == [
        str(first.run_id),
        str(second.run_id),
    ]
    assert observed.metadata["clawith_run_id"] == str(second.run_id)


@pytest.mark.asyncio
async def test_two_direct_runs_keep_one_thread_running_summary() -> None:
    thread_id = str(uuid.uuid4())
    first = replace(_run(uuid.uuid4()), thread_id=thread_id)
    second_base = _run(uuid.uuid4())
    second = replace(
        second_base,
        tenant_id=first.tenant_id,
        thread_id=thread_id,
    )
    driver = _driver(SummaryCompletingExecutor())

    await driver.execute(
        connection=_connection(),
        run=first,
        command=_command(first, "start"),
        checkpoint=None,
    )
    await driver.execute(
        connection=_connection(),
        run=second,
        command=_command(second, "start"),
        checkpoint=None,
    )

    observed = await driver.read_latest(connection=_connection(), run=second)

    assert observed is not None
    assert observed.state["thread_summary"]["task_goal_and_constraints"] == (
        "preserve across Runs"
    )
    assert observed.state["summary_covered_through_message_id"] == (
        "summary-boundary"
    )


@pytest.mark.asyncio
async def test_resume_validates_wait_contract_and_uses_its_own_metadata() -> None:
    run = _run(uuid.uuid4())
    start = _command(run, "start")
    driver = _driver(WaitingExecutor())
    await driver.execute(connection=_connection(), run=run, command=start, checkpoint=None)
    waiting = await driver.read_latest(connection=_connection(), run=run)
    assert waiting is not None
    assert waiting.state["lifecycle"]["status"] == "waiting_user"

    resume = _command(
        run,
        "resume",
        payload={
            "resume_type": "user_input",
            "correlation_id": "correlation-1",
            "payload": {"confirmed": True},
        },
    )
    await driver.execute(
        connection=_connection(),
        run=run,
        command=resume,
        checkpoint=waiting,
    )
    completed = await driver.read_latest(connection=_connection(), run=run)

    assert completed is not None
    assert completed.state["lifecycle"]["status"] == "completed"
    assert completed.metadata["clawith_run_id"] == str(run.run_id)
    assert completed.metadata["clawith_command_id"] == str(resume.id)
    assert await driver.read_for_command(
        connection=_connection(),
        run=run,
        command=resume,
    ) == completed


@pytest.mark.asyncio
async def test_resume_rejects_a_mismatched_correlation_without_advancing() -> None:
    run = _run(uuid.uuid4())
    start = _command(run, "start")
    driver = _driver(WaitingExecutor())
    await driver.execute(connection=_connection(), run=run, command=start, checkpoint=None)
    waiting = await driver.read_latest(connection=_connection(), run=run)
    assert waiting is not None
    resume = _command(
        run,
        "resume",
        payload={
            "resume_type": "user_input",
            "correlation_id": "wrong-correlation",
            "payload": {},
        },
    )

    with pytest.raises(CommandExecutionRejected) as exc_info:
        await driver.execute(
            connection=_connection(),
            run=run,
            command=resume,
            checkpoint=waiting,
        )

    assert exc_info.value.code == "resume_correlation_mismatch"
    unchanged = await driver.read_latest(connection=_connection(), run=run)
    assert unchanged is not None
    assert unchanged.metadata["clawith_command_id"] == str(start.id)
    assert await driver.read_for_command(
        connection=_connection(),
        run=run,
        command=resume,
    ) is None


@pytest.mark.asyncio
async def test_cancel_is_rejected_by_driver_and_preserves_wait_checkpoint() -> None:
    run = _run(uuid.uuid4())
    start = _command(run, "start")
    driver = _driver(WaitingExecutor())
    await driver.execute(connection=_connection(), run=run, command=start, checkpoint=None)
    waiting = await driver.read_latest(connection=_connection(), run=run)
    assert waiting is not None
    cancel = _command(run, "cancel", payload={"reason": "user_abort"})

    with pytest.raises(CommandExecutionRejected) as raised:
        await driver.execute(
            connection=_connection(),
            run=run,
            command=cancel,
            checkpoint=waiting,
        )

    assert raised.value.code == "cancel_is_control_plane"
    preserved = await driver.read_latest(connection=_connection(), run=run)
    assert preserved == waiting
    assert preserved.state["lifecycle"]["status"] == "waiting_user"


@pytest.mark.asyncio
async def test_driver_uses_current_graph_for_old_observational_identity() -> None:
    run = _run(uuid.uuid4())
    run = replace(
        run,
        graph_name="legacy-runtime-name",
        graph_version="old-version",
    )
    driver = _driver(CompletingExecutor())
    command = _command(run, "start")

    await driver.execute(
        connection=_connection(),
        run=run,
        command=command,
        checkpoint=None,
    )
    observed = await driver.read_latest(connection=_connection(), run=run)

    assert observed is not None
    assert observed.state["lifecycle"]["status"] == "completed"
    assert observed.metadata["clawith_run_id"] == str(run.run_id)


@pytest.mark.asyncio
async def test_driver_resumes_old_checkpoint_with_current_compatible_graph() -> None:
    checkpointer = InMemorySaver()
    run = _run(uuid.uuid4())
    old_graph = build_agent_runtime_graph(
        checkpointer=checkpointer,
        settings=_settings(),
    )
    old_driver = LangGraphRuntimeDriver(
        graph_registry=RuntimeGraphRegistry([old_graph]),
        snapshot_factory=StaticRuntimeInputSnapshotFactory(_snapshots()),
        node_executor=cast(RuntimeNodeExecutor, WaitingExecutor()),
    )
    start = _command(run, "start")
    await old_driver.execute(
        connection=_connection(),
        run=run,
        command=start,
        checkpoint=None,
    )
    waiting = await old_driver.read_latest(connection=_connection(), run=run)
    assert waiting is not None
    assert waiting.state["lifecycle"]["status"] == "waiting_user"

    current_graph = build_agent_runtime_graph(
        checkpointer=checkpointer,
        settings=_settings(
            graph_name="renamed-current-driver-graph",
            graph_version="v2",
        ),
    )
    current_driver = LangGraphRuntimeDriver(
        graph_registry=RuntimeGraphRegistry([current_graph]),
        snapshot_factory=StaticRuntimeInputSnapshotFactory(_snapshots()),
        node_executor=cast(RuntimeNodeExecutor, WaitingExecutor()),
    )
    resume = _command(
        run,
        "resume",
        payload={
            "resume_type": "user_input",
            "correlation_id": "correlation-1",
            "payload": {"confirmed": True},
        },
    )

    await current_driver.execute(
        connection=_connection(),
        run=run,
        command=resume,
        checkpoint=waiting,
    )
    completed = await current_driver.read_latest(
        connection=_connection(),
        run=run,
    )

    assert completed is not None
    assert completed.state["lifecycle"]["status"] == "completed"
    assert completed.metadata["clawith_command_id"] == str(resume.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "run_kind", "goal", "initial_input"),
    [
        (
            "task",
            "background",
            "Prepare the weekly risk report",
            {"task_id": "task-1"},
        ),
        (
            "heartbeat",
            "background",
            "Review current activity",
            {"background_mode": "heartbeat", "heartbeat_context": {}},
        ),
        (
            "heartbeat",
            "background",
            "Run the one-shot audit",
            {"background_mode": "oneshot", "oneshot_prompt": "duplicate-data"},
        ),
        (
            "heartbeat",
            "background",
            "[自动调度任务] Reconcile reports",
            {"background_mode": "schedule", "schedule_instruction": "duplicate-data"},
        ),
        (
            "chat",
            "foreground",
            "Validate the assigned planning responsibility",
            {
                "message_id": "group-trigger-message",
                "current_responsibility": "duplicate-data",
            },
        ),
    ],
)
async def test_driver_labels_goal_when_run_has_no_durable_user_input(
    source_type: str,
    run_kind: str,
    goal: str,
    initial_input: dict[str, JsonValue],
) -> None:
    run = replace(
        _run(uuid.uuid4()),
        source_type=source_type,
        run_kind=run_kind,
        goal=goal,
    )
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    driver = LangGraphRuntimeDriver(
        graph_registry=RuntimeGraphRegistry([graph]),
        snapshot_factory=StaticRuntimeInputSnapshotFactory(
            _snapshots(initial_input=initial_input)
        ),
        node_executor=cast(RuntimeNodeExecutor, CompletingExecutor()),
    )

    await driver.execute(
        connection=_connection(),
        run=run,
        command=_command(run, "start"),
        checkpoint=None,
    )
    observed = await driver.read_latest(connection=_connection(), run=run)

    assert observed is not None
    messages = runtime_messages_as_json(observed.state)
    assert messages[-1]["content"] == f"Current Run Directive:\n{goal}"
    assert messages[-1]["runtime_input"] == "current"
