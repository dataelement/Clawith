"""Compile and routing tests for the real LangGraph Runtime skeleton."""

from dataclasses import FrozenInstanceError
from typing import cast
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import pytest

from app.config import Settings
from app.services.agent_runtime.checkpointer import runtime_thread_config
from app.services.agent_runtime.graph import (
    RuntimeGraphContractError,
    RuntimeGraphIdentity,
    build_agent_runtime_graph,
    route_after_control,
)
from app.services.agent_runtime.tool_execution import RetryableToolNodeError
from app.services.agent_runtime.state import (
    ControlRoute,
    JsonValue,
    LifecycleStatus,
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeExecutor,
    RuntimeNodeName,
    RuntimeStateUpdate,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_GRAPH_NAME="test_agent_runtime",
        AGENT_RUNTIME_GRAPH_VERSION="v-test",
    )


def _state(
    run_id: uuid.UUID,
    *,
    status: str = "running",
    route: str = "model",
    waiting_request: dict[str, JsonValue] | None = None,
) -> RuntimeGraphState:
    return {
        "registry": RunRegistrySnapshot(
            tenant_id="tenant-1",
            run_id=str(run_id),
            goal="Complete the requested work",
            run_kind="foreground",
            source_type="chat",
            model_id="model-1",
            graph_name="test_agent_runtime",
            graph_version="v-test",
            agent_id="agent-1",
            session_id="session-1",
        ),
        "snapshots": RunInputSnapshots(
            session_context={"summary": "stable context"},
            session_context_version=3,
            recent_session_messages=({"role": "user", "content": "go"},),
            related_run_summaries=(),
            initial_input={"message_id": "message-1"},
        ),
        "lifecycle": {
            "status": cast(LifecycleStatus, status),
            "next_route": cast(ControlRoute, route),
            "waiting_request": waiting_request,
        },
    }


class CompletingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[RuntimeNodeName, JsonValue | None]] = []

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context
        self.calls.append((node, resume_value))
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


class WaitingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[RuntimeNodeName, JsonValue | None]] = []

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context
        self.calls.append((node, resume_value))
        if node == "wait":
            return {
                "lifecycle": {
                    "status": "completed",
                    "next_route": "terminal",
                    "final_answer": str(resume_value),
                }
            }
        return {"lifecycle": dict(state["lifecycle"])}


class InvalidTerminalExecutor:
    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context, resume_value
        if node == "terminal":
            return {"lifecycle": {"status": "running", "next_route": "model"}}
        return {"lifecycle": dict(state["lifecycle"])}


class RetryingToolExecutor:
    def __init__(self) -> None:
        self.calls: list[RuntimeNodeName] = []
        self.tool_attempts = 0

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate:
        del context, resume_value
        self.calls.append(node)
        if node == "tool":
            self.tool_attempts += 1
            if self.tool_attempts < 3:
                raise RetryableToolNodeError(
                    tool_call_id="call-retry",
                    error_code="temporary_read_failure",
                )
            return {
                "lifecycle": {
                    "status": "completed",
                    "next_route": "terminal",
                    "final_answer": "done",
                }
            }
        return {"lifecycle": dict(state["lifecycle"])}


def _context(run_id: uuid.UUID, executor: object, *, command_id: str) -> RuntimeContext:
    return RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(run_id),
        command_id=command_id,
        executor=cast(RuntimeNodeExecutor, executor),
        graph_name="test_agent_runtime",
        graph_version="v-test",
        actor_user_id="user-1",
    )


def test_registry_and_input_snapshots_are_frozen() -> None:
    run_id = uuid.uuid4()
    state = _state(run_id)

    with pytest.raises(FrozenInstanceError):
        state["registry"].goal = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state["snapshots"].session_context_version = 4  # type: ignore[misc]


def test_planning_identity_is_separate_but_uses_the_same_version_contract() -> None:
    identity = RuntimeGraphIdentity.planning_from_settings(_settings())
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
        identity=identity,
    )

    assert identity.name == "test_agent_runtime_group_planning"
    assert identity.version == "v-test"
    assert graph.compiled.name == "test_agent_runtime_group_planning@v-test"


@pytest.mark.parametrize(
    ("status", "route"),
    [
        ("running", "model"),
        ("running", "compact"),
        ("running", "tool"),
        ("verifying", "verify"),
        ("waiting_user", "wait"),
        ("waiting_user", "compact"),
        ("waiting_external", "wait"),
        ("waiting_agent", "wait"),
        ("completed", "terminal"),
        ("failed", "terminal"),
        ("cancelled", "terminal"),
    ],
)
def test_control_route_accepts_only_valid_lifecycle_pairs(
    status: str,
    route: str,
) -> None:
    assert route_after_control(_state(uuid.uuid4(), status=status, route=route)) == route


@pytest.mark.parametrize(
    ("status", "route"),
    [("running", "terminal"), ("completed", "model"), ("created", "model")],
)
def test_control_route_rejects_invalid_lifecycle_pairs(
    status: str,
    route: str,
) -> None:
    with pytest.raises(RuntimeGraphContractError):
        route_after_control(_state(uuid.uuid4(), status=status, route=route))


def test_control_route_rejects_unknown_route() -> None:
    state = _state(uuid.uuid4())
    state["lifecycle"]["next_route"] = cast(ControlRoute, "projected_status")

    with pytest.raises(RuntimeGraphContractError, match="Unsupported control route"):
        route_after_control(state)


@pytest.mark.asyncio
async def test_graph_compiles_from_settings_and_checkpoints_terminal_lifecycle() -> None:
    run_id = uuid.uuid4()
    executor = CompletingExecutor()
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)

    result = await graph.compiled.ainvoke(
        _state(run_id),
        config,
        context=_context(run_id, executor, command_id="command-1"),
    )
    snapshot = await graph.compiled.aget_state(config)

    assert graph.identity.name == "test_agent_runtime"
    assert graph.identity.version == "v-test"
    assert graph.compiled.name == "test_agent_runtime@v-test"
    assert result["lifecycle"]["status"] == "completed"
    assert snapshot.values["lifecycle"]["status"] == "completed"
    assert "last_applied_command_ids" not in snapshot.values["lifecycle"]
    assert executor.calls == [
        ("control_guard", None),
        ("model", None),
        ("control_guard", None),
        ("verify", None),
        ("control_guard", None),
        ("terminal", None),
    ]


@pytest.mark.asyncio
async def test_wait_node_interrupts_and_resumes_the_same_thread() -> None:
    run_id = uuid.uuid4()
    executor = WaitingExecutor()
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)
    initial = _state(
        run_id,
        status="waiting_user",
        route="wait",
        waiting_request={"waiting_type": "user", "reason": "confirm"},
    )

    interrupted = await graph.compiled.ainvoke(
        initial,
        config,
        context=_context(run_id, executor, command_id="command-start"),
    )
    waiting_snapshot = await graph.compiled.aget_state(config)

    assert interrupted["lifecycle"]["status"] == "waiting_user"
    assert waiting_snapshot.next == ("wait",)
    assert "last_applied_command_ids" not in waiting_snapshot.values["lifecycle"]
    assert executor.calls == [("control_guard", None)]

    resumed = await graph.compiled.ainvoke(
        Command(resume={"confirmed": True}),
        config,
        context=_context(run_id, executor, command_id="command-resume"),
    )

    assert resumed["lifecycle"]["status"] == "completed"
    resumed_snapshot = await graph.compiled.aget_state(config)
    assert "last_applied_command_ids" not in resumed_snapshot.values["lifecycle"]
    assert executor.calls == [
        ("control_guard", None),
        ("wait", {"confirmed": True}),
        ("control_guard", None),
        ("terminal", None),
    ]


@pytest.mark.asyncio
async def test_graph_executes_new_state_without_registry_injection() -> None:
    run_id = uuid.uuid4()
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )

    state = _state(run_id)
    state.pop("registry")

    result = await graph.compiled.ainvoke(
        state,
        runtime_thread_config(run_id),
        context=_context(run_id, CompletingExecutor(), command_id="command-1"),
    )

    assert result["lifecycle"]["status"] == "completed"
    assert "registry" not in result


@pytest.mark.asyncio
async def test_tool_node_uses_langgraph_retry_policy_without_checkpointing_failures(
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("langgraph.pregel._retry.asyncio.sleep", no_sleep)
    run_id = uuid.uuid4()
    executor = RetryingToolExecutor()
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )

    result = await graph.compiled.ainvoke(
        _state(run_id, route="tool"),
        runtime_thread_config(run_id),
        context=_context(run_id, executor, command_id="command-retry"),
    )

    assert result["lifecycle"]["status"] == "completed"
    assert executor.tool_attempts == 3
    assert executor.calls == [
        "control_guard",
        "tool",
        "tool",
        "tool",
        "control_guard",
        "terminal",
    ]


@pytest.mark.asyncio
async def test_terminal_node_cannot_end_with_an_active_lifecycle() -> None:
    run_id = uuid.uuid4()
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )

    with pytest.raises(RuntimeGraphContractError, match="must preserve"):
        await graph.compiled.ainvoke(
            _state(run_id, status="completed", route="terminal"),
            runtime_thread_config(run_id),
            context=_context(run_id, InvalidTerminalExecutor(), command_id="command-1"),
        )


@pytest.mark.asyncio
async def test_graph_drops_legacy_checkpoint_command_receipts() -> None:
    run_id = uuid.uuid4()
    state = _state(run_id, status="completed", route="terminal")
    state["lifecycle"]["last_applied_command_ids"] = [f"command-{index}" for index in range(70)]
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )

    await graph.compiled.ainvoke(
        state,
        runtime_thread_config(run_id),
        context=_context(run_id, CompletingExecutor(), command_id="command-current"),
    )
    snapshot = await graph.compiled.aget_state(runtime_thread_config(run_id))

    assert "last_applied_command_ids" not in snapshot.values["lifecycle"]
