"""Deterministic Runtime node executor integration tests."""

from __future__ import annotations

from collections import deque
from typing import cast
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import pytest

from app.config import Settings
from app.services.agent_runtime.checkpointer import runtime_thread_config
from app.services.agent_runtime.graph import build_agent_runtime_graph
from app.services.agent_runtime.node_executor import (
    CancelSignal,
    DeterministicRuntimeNodeExecutor,
    FinalizationResult,
    ModelStepResult,
    ToolStepResult,
    VerificationResult,
)
from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeExecutor,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_GRAPH_NAME="node_executor_test",
        AGENT_RUNTIME_GRAPH_VERSION="v1",
    )


def _state(run_id: uuid.UUID) -> RuntimeGraphState:
    return {
        "registry": RunRegistrySnapshot(
            tenant_id="tenant-1",
            run_id=str(run_id),
            goal="Complete the requested work",
            run_kind="foreground",
            source_type="chat",
            model_id="model-1",
            graph_name="node_executor_test",
            graph_version="v1",
            agent_id="agent-1",
            session_id="session-1",
        ),
        "snapshots": RunInputSnapshots(
            session_context={"summary": "stable context"},
            session_context_version=1,
            recent_session_messages=({"role": "user", "content": "go"},),
            related_run_summaries=(),
            initial_input={"message_id": "message-1"},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "model",
            "run_messages": [],
            "pending_tool_calls": [],
        },
    }


class CancelSource:
    def __init__(self, signal: CancelSignal | None = None) -> None:
        self.signal = signal
        self.calls = 0

    async def get_cancel(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> CancelSignal | None:
        del state, context
        self.calls += 1
        signal, self.signal = self.signal, None
        return signal


class ModelService:
    def __init__(self, *results: ModelStepResult) -> None:
        self.results = deque(results)
        self.calls = 0

    async def complete_once(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> ModelStepResult:
        del state, context
        self.calls += 1
        return self.results.popleft()


class ToolService:
    def __init__(self, result: ToolStepResult | None = None) -> None:
        self.result = result or ToolStepResult()
        self.calls: list[tuple[JsonObject, ...]] = []

    async def execute_pending(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        tool_calls: tuple[JsonObject, ...],
    ) -> ToolStepResult:
        del state, context
        self.calls.append(tool_calls)
        return self.result


class Verifier:
    def __init__(self, *results: VerificationResult) -> None:
        self.results = deque(results)
        self.calls: list[str] = []

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult:
        del state, context
        self.calls.append(candidate)
        return self.results.popleft()


class Finalizer:
    async def finalize(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        answer: str,
        verification: VerificationResult,
    ) -> FinalizationResult:
        del state, context, verification
        return FinalizationResult(
            result_summary={"summary": answer, "artifact_refs": ["artifact-1"]},
            session_context_delta={"decisions": [answer]},
            delivery_request={"content": answer},
        )


class TerminalHandler:
    def __init__(self) -> None:
        self.states: list[RuntimeGraphState] = []

    async def handle_terminal(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> None:
        del context
        self.states.append(state)


def _executor(
    model: ModelService,
    *,
    cancel: CancelSource | None = None,
    tools: ToolService | None = None,
    verifier: Verifier | None = None,
    terminal: TerminalHandler | None = None,
    max_model_steps: int = 50,
    max_verification_repairs: int = 2,
) -> tuple[DeterministicRuntimeNodeExecutor, TerminalHandler]:
    terminal_handler = terminal or TerminalHandler()
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=cancel or CancelSource(),
        model_service=model,
        tool_service=tools or ToolService(),
        verifier=verifier,
        finalizer=Finalizer(),
        terminal_handler=terminal_handler,
        max_model_steps=max_model_steps,
        max_verification_repairs=max_verification_repairs,
    )
    return executor, terminal_handler


def _context(
    run_id: uuid.UUID,
    executor: DeterministicRuntimeNodeExecutor,
    command_id: str,
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(run_id),
        command_id=command_id,
        executor=cast(RuntimeNodeExecutor, executor),
        actor_user_id="user-1",
    )


async def _invoke(
    run_id: uuid.UUID,
    executor: DeterministicRuntimeNodeExecutor,
    *,
    command_id: str = "command-1",
) -> dict[str, JsonValue]:
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    return await graph.compiled.ainvoke(
        _state(run_id),
        runtime_thread_config(run_id),
        context=_context(run_id, executor, command_id),
    )


@pytest.mark.asyncio
async def test_finish_is_verified_finalized_and_delivered_after_checkpoint_transition() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="finish",
            assistant_message={"role": "assistant", "content": "done"},
            finish_content="done",
        )
    )
    verifier = Verifier(VerificationResult(outcome="pass", details={"code": "ok"}))
    executor, terminal = _executor(model, verifier=verifier)

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["next_route"] == "terminal"
    assert lifecycle["model_step_count"] == 1
    assert lifecycle["result_summary"] == {
        "summary": "done",
        "artifact_refs": ["artifact-1"],
    }
    assert lifecycle["session_context_delta"] == {"decisions": ["done"]}
    assert lifecycle["delivery_request"] == {"content": "done"}
    assert lifecycle["last_applied_command_ids"] == ["command-1"]
    assert verifier.calls == ["done"]
    assert len(terminal.states) == 1
    assert terminal.states[0]["lifecycle"]["status"] == "completed"


@pytest.mark.asyncio
async def test_tool_batch_is_executed_before_the_next_model_step() -> None:
    run_id = uuid.uuid4()
    tool_call: JsonObject = {
        "id": "call-1",
        "name": "lookup",
        "arguments": {"query": "answer"},
    }
    model = ModelService(
        ModelStepResult(
            intent="tool_calls",
            assistant_message={"role": "assistant", "tool_calls": [tool_call]},
            tool_calls=(tool_call,),
        ),
        ModelStepResult(intent="finish", finish_content="tool-backed answer"),
    )
    tools = ToolService(ToolStepResult(messages=({"role": "tool", "tool_call_id": "call-1", "content": "result"},)))
    executor, _ = _executor(model, tools=tools)

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["model_step_count"] == 2
    assert lifecycle["pending_tool_calls"] == []
    assert tools.calls == [(tool_call,)]
    assert lifecycle["run_messages"] == [
        {"role": "assistant", "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]


@pytest.mark.asyncio
async def test_wait_interrupt_resumes_the_same_run_and_then_finishes() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="wait",
            waiting_request={
                "waiting_type": "user",
                "correlation_id": "correlation-1",
                "question": "Continue?",
            },
        ),
        ModelStepResult(intent="finish", finish_content="resumed"),
    )
    executor, terminal = _executor(model)
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)

    interrupted = await graph.compiled.ainvoke(
        _state(run_id),
        config,
        context=_context(run_id, executor, "command-start"),
    )

    assert interrupted["lifecycle"]["status"] == "waiting_user"
    assert terminal.states == []
    waiting = await graph.compiled.aget_state(config)
    assert waiting.next == ("wait",)

    resumed = await graph.compiled.ainvoke(
        Command(resume={"confirmed": True}),
        config,
        context=_context(run_id, executor, "command-resume"),
    )

    lifecycle = resumed["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["waiting_request"] is None
    assert lifecycle["last_applied_command_ids"] == [
        "command-start",
        "command-resume",
    ]
    assert lifecycle["run_messages"] == [
        {
            "id": str(uuid.uuid5(run_id, "resume:command-resume")),
            "role": "user",
            "content": {"confirmed": True},
            "runtime_input": "resume",
        }
    ]
    assert len(terminal.states) == 1


@pytest.mark.asyncio
async def test_cancel_is_observed_before_the_model_or_a_new_tool_can_start() -> None:
    run_id = uuid.uuid4()
    model = ModelService(ModelStepResult(intent="finish", finish_content="too late"))
    cancel = CancelSource(CancelSignal(command_id="cancel-1", reason="user_abort"))
    executor, terminal = _executor(model, cancel=cancel)

    result = await _invoke(run_id, executor, command_id="worker-command")

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "cancelled"
    assert lifecycle["reason"] == "user_abort"
    assert lifecycle["last_applied_command_ids"] == ["cancel-1", "worker-command"]
    assert model.calls == 0
    assert len(terminal.states) == 1


@pytest.mark.asyncio
async def test_plain_text_repair_stops_at_the_model_step_limit() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="text",
            assistant_message={"role": "assistant", "content": "plain text"},
        )
    )
    executor, terminal = _executor(model, max_model_steps=1)

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "failed"
    assert lifecycle["reason"] == "model_step_limit_reached"
    assert lifecycle["model_step_count"] == 1
    assert model.calls == 1
    assert lifecycle["run_messages"][-1]["role"] == "user"
    assert len(terminal.states) == 1


@pytest.mark.asyncio
async def test_verification_repairs_are_bounded() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(intent="finish", finish_content="first"),
        ModelStepResult(intent="finish", finish_content="second"),
    )
    verifier = Verifier(
        VerificationResult(outcome="repair", reason="add evidence"),
        VerificationResult(outcome="repair", reason="still incomplete"),
    )
    executor, terminal = _executor(
        model,
        verifier=verifier,
        max_verification_repairs=1,
    )

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "failed"
    assert lifecycle["reason"] == "verification_repair_limit_reached"
    assert lifecycle["verification_attempt_count"] == 2
    assert lifecycle["run_messages"] == [
        {
            "id": str(uuid.uuid5(run_id, "verification:1:repair")),
            "role": "user",
            "content": "add evidence",
        }
    ]
    assert verifier.calls == ["first", "second"]
    assert len(terminal.states) == 1
