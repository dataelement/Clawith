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
    DefaultRuntimeFinalizer,
    DeterministicRuntimeNodeExecutor,
    FinalizationResult,
    ModelStepResult,
    RunCompactResult,
    RuntimeInvocationCancelled,
    RuntimeNodeTransitionError,
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
    runtime_messages_as_json,
)
from app.services.agent_runtime.tool_execution import RetryableToolNodeError


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
        "messages": [],
        "lifecycle": {
            "status": "running",
            "next_route": "model",
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


class PerCallRetryingToolService:
    """Fail each receipt twice so LangGraph must budget retries per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[JsonObject, ...]] = []
        self.attempts: dict[str, int] = {}

    async def execute_pending(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        tool_calls: tuple[JsonObject, ...],
    ) -> ToolStepResult:
        del state, context
        self.calls.append(tool_calls)
        messages: list[JsonObject] = []
        for call in tool_calls:
            call_id = str(call["id"])
            attempt = self.attempts.get(call_id, 0) + 1
            self.attempts[call_id] = attempt
            if attempt < 3:
                raise RetryableToolNodeError(
                    tool_call_id=call_id,
                    error_code="temporary_read_failure",
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"result:{call_id}",
                }
            )
        return ToolStepResult(messages=tuple(messages))


class WaitingAgentThenTailToolService:
    def __init__(self) -> None:
        self.calls: list[tuple[JsonObject, ...]] = []

    async def execute_pending(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        tool_calls: tuple[JsonObject, ...],
    ) -> ToolStepResult:
        del state, context
        self.calls.append(tool_calls)
        call = tool_calls[0]
        call_id = str(call["id"])
        message: JsonObject = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"result:{call_id}",
        }
        if call_id == "call-agent":
            return ToolStepResult(
                messages=(message,),
                waiting_request={
                    "waiting_type": "agent",
                    "correlation_id": "a2a:consult:00000000-0000-0000-0000-000000000001",
                    "reason": "waiting_for_consult",
                },
            )
        return ToolStepResult(messages=(message,))


class RunCompactor:
    def __init__(self, result: RunCompactResult | None = None) -> None:
        self.result = result or RunCompactResult()
        self.calls = 0

    async def compact_if_needed(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
    ) -> RunCompactResult:
        del state, context
        self.calls += 1
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


@pytest.mark.asyncio
async def test_default_finalizer_emits_a_source_bound_session_delta() -> None:
    run_id = uuid.uuid4()
    context = RuntimeContext(
        tenant_id=str(uuid.uuid4()),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=cast(RuntimeNodeExecutor, object()),
    )
    finalized = await DefaultRuntimeFinalizer().finalize(
        _state(run_id),
        context,
        "Verified answer",
        VerificationResult(
            outcome="pass",
            details={
                "code": "ok",
                "artifact_refs": ["artifact://verified"],
                "evidence_refs": ["evidence://verified"],
            },
        ),
    )

    assert finalized.result_summary["artifact_refs"] == ["artifact://verified"]
    assert finalized.result_summary["evidence_refs"] == ["evidence://verified"]
    assert finalized.session_context_delta == {
        "source_run_id": str(run_id),
        "new_requirements": [],
        "new_decisions": [],
        "resolved_open_items": [],
        "new_open_items": [],
        "evidence_refs": ["evidence://verified"],
        "workspace_refs": [],
        "result_summary": "Verified answer",
    }


@pytest.mark.asyncio
async def test_group_finish_intent_is_frozen_into_terminal_delivery_request() -> None:
    run_id = uuid.uuid4()
    intent: JsonObject = {
        "version": 1,
        "source_run_id": str(run_id),
        "mention_participant_ids": [str(uuid.uuid4())],
        "idempotency_key": f"run:{run_id}:terminal:completed",
    }
    state = _state(run_id)
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=CancelSource(),
        model_service=ModelService(
            ModelStepResult(
                intent="finish",
                finish_content="Public handoff reply",
                finish_delivery_intent=intent,
            )
        ),
        tool_service=ToolService(),
        verifier=Verifier(VerificationResult(outcome="pass", details={"code": "ok"})),
    )
    context = _context(run_id, executor, "command-group-handoff")

    model_update = await executor.execute("model", state, context)
    verifying_state = cast(
        RuntimeGraphState,
        {**state, "lifecycle": model_update["lifecycle"]},
    )
    assert verifying_state["lifecycle"]["finish_delivery_intent"] == intent

    verify_update = await executor.execute("verify", verifying_state, context)
    lifecycle = verify_update["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["delivery_request"] == {
        "content": "Public handoff reply",
        "group_handoff": intent,
    }
    assert "finish_delivery_intent" not in lifecycle


def _executor(
    model: ModelService,
    *,
    cancel: CancelSource | None = None,
    tools: ToolService | None = None,
    run_compactor: RunCompactor | None = None,
    verifier: Verifier | None = None,
    max_verification_repairs: int = 2,
) -> DeterministicRuntimeNodeExecutor:
    return DeterministicRuntimeNodeExecutor(
        cancel_source=cancel or CancelSource(),
        model_service=model,
        tool_service=tools or ToolService(),
        run_compactor=run_compactor,
        verifier=verifier,
        finalizer=Finalizer(),
        max_verification_repairs=max_verification_repairs,
    )


@pytest.mark.asyncio
async def test_compact_atomically_replaces_thread_summary_and_covered_messages() -> None:
    run_id = uuid.uuid4()
    retained = {"id": "recent-1", "role": "user", "content": "recent"}
    compactor = RunCompactor(
        RunCompactResult(
            compacted=True,
            thread_summary={
                "task_goal_and_constraints": "done",
                "completed_work_and_results": "done",
                "key_decisions_and_evidence": "",
                "unfinished_or_blocked": "",
                "next_actions": "continue",
            },
            recent_messages=(retained,),
            covered_through_message_id="old-boundary",
        )
    )
    executor = _executor(ModelService(), run_compactor=compactor)
    state = _state(run_id)
    state["lifecycle"].update(
        {
            "next_route": "compact",
            "pending_tool_calls": [{"id": "pending-exact"}],
            "waiting_request": {"correlation_id": "wait-exact"},
            "verification_result": {"outcome": "repair"},
        }
    )

    update = await executor.execute(
        "compact",
        state,
        _context(run_id, executor, "command-compact"),
    )

    lifecycle = update["lifecycle"]
    assert compactor.calls == 1
    assert lifecycle["next_route"] == "model"
    assert update["thread_summary"]["next_actions"] == "continue"
    assert update["summary_covered_through_message_id"] == "old-boundary"
    assert update["messages"][-1] == retained
    assert lifecycle["pending_tool_calls"] == [{"id": "pending-exact"}]
    assert lifecycle["waiting_request"] == {"correlation_id": "wait-exact"}
    assert lifecycle["verification_result"] == {"outcome": "repair"}


@pytest.mark.asyncio
async def test_compact_is_rejected_outside_the_pre_model_running_boundary() -> None:
    run_id = uuid.uuid4()
    compactor = RunCompactor()
    executor = _executor(ModelService(), run_compactor=compactor)
    state = _state(run_id)
    state["lifecycle"].update(
        {
            "status": "waiting_user",
            "next_route": "compact",
        }
    )

    with pytest.raises(RuntimeNodeTransitionError) as raised:
        await executor.execute(
            "compact",
            state,
            _context(run_id, executor, "command-compact"),
        )

    assert raised.value.code == "invalid_compact_status"
    assert compactor.calls == 0


def _context(
    run_id: uuid.UUID,
    executor: DeterministicRuntimeNodeExecutor,
    command_id: str,
    *,
    model_turn_limit: int | None = 50,
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(run_id),
        command_id=command_id,
        executor=cast(RuntimeNodeExecutor, executor),
        graph_name="node_executor_test",
        graph_version="v1",
        model_turn_limit=model_turn_limit,
        actor_user_id="user-1",
    )


async def _invoke(
    run_id: uuid.UUID,
    executor: DeterministicRuntimeNodeExecutor,
    *,
    command_id: str = "command-1",
    model_turn_limit: int | None = 50,
) -> dict[str, JsonValue]:
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    return await graph.compiled.ainvoke(
        _state(run_id),
        runtime_thread_config(run_id),
        context=_context(
            run_id,
            executor,
            command_id,
            model_turn_limit=model_turn_limit,
        ),
    )


@pytest.mark.asyncio
async def test_finish_is_verified_and_finalized_into_terminal_checkpoint_state() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="finish",
            assistant_message={"role": "assistant", "content": "done"},
            finish_content="done",
        )
    )
    verifier = Verifier(VerificationResult(outcome="pass", details={"code": "ok"}))
    executor = _executor(model, verifier=verifier)

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
    assert "last_applied_command_ids" not in lifecycle
    assert verifier.calls == ["done"]


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
    executor = _executor(model, tools=tools)

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["model_step_count"] == 2
    assert lifecycle["pending_tool_calls"] == []
    assert tools.calls == [(tool_call,)]
    messages = runtime_messages_as_json(cast(RuntimeGraphState, result))
    assert [message["role"] for message in messages] == ["assistant", "tool"]
    assert messages[0]["tool_calls"][0]["id"] == "call-1"  # type: ignore[index]
    assert messages[1]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_each_tool_call_gets_an_independent_langgraph_retry_budget(
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("langgraph.pregel._retry.asyncio.sleep", no_sleep)
    run_id = uuid.uuid4()
    tool_calls: tuple[JsonObject, ...] = (
        {"id": "call-1", "name": "lookup", "arguments": {"query": "one"}},
        {"id": "call-2", "name": "lookup", "arguments": {"query": "two"}},
    )
    model = ModelService(
        ModelStepResult(
            intent="tool_calls",
            assistant_message={"role": "assistant", "tool_calls": list(tool_calls)},
            tool_calls=tool_calls,
        ),
        ModelStepResult(intent="finish", finish_content="both reads completed"),
    )
    tools = PerCallRetryingToolService()
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=CancelSource(),
        model_service=model,
        tool_service=tools,
        finalizer=Finalizer(),
    )

    result = await _invoke(run_id, executor)

    assert result["lifecycle"]["status"] == "completed"
    assert result["lifecycle"]["pending_tool_calls"] == []
    assert tools.attempts == {"call-1": 3, "call-2": 3}
    assert tools.calls == [
        (tool_calls[0],),
        (tool_calls[0],),
        (tool_calls[0],),
        (tool_calls[1],),
        (tool_calls[1],),
        (tool_calls[1],),
    ]
    messages = runtime_messages_as_json(cast(RuntimeGraphState, result))
    assert [message["tool_call_id"] for message in messages if message["role"] == "tool"] == [
        "call-1",
        "call-2",
    ]


@pytest.mark.asyncio
async def test_duplicate_tool_call_ids_fail_before_any_provider_execution() -> None:
    run_id = uuid.uuid4()
    duplicate_calls: tuple[JsonObject, ...] = (
        {"id": "call-duplicate", "name": "write", "arguments": {"value": 1}},
        {"id": "call-duplicate", "name": "write", "arguments": {"value": 2}},
    )
    model = ModelService(
        ModelStepResult(
            intent="tool_calls",
            assistant_message={
                "role": "assistant",
                "tool_calls": list(duplicate_calls),
            },
            tool_calls=duplicate_calls,
        )
    )
    tools = ToolService()
    executor = _executor(model, tools=tools)

    result = await _invoke(run_id, executor)

    assert result["lifecycle"]["status"] == "failed"
    assert result["lifecycle"]["error"] == {
        "code": "invalid_tool_call",
        "message": "pending tool calls require unique non-empty IDs",
    }
    assert tools.calls == []


@pytest.mark.asyncio
async def test_waiting_agent_resume_finishes_tail_before_returning_to_model() -> None:
    run_id = uuid.uuid4()
    tool_calls: tuple[JsonObject, ...] = (
        {"id": "call-agent", "name": "delegate", "arguments": {}},
        {"id": "call-tail", "name": "lookup", "arguments": {}},
    )
    model = ModelService(
        ModelStepResult(
            intent="tool_calls",
            assistant_message={"role": "assistant", "tool_calls": list(tool_calls)},
            tool_calls=tool_calls,
        ),
        ModelStepResult(intent="finish", finish_content="collaboration complete"),
    )
    tools = WaitingAgentThenTailToolService()
    executor = DeterministicRuntimeNodeExecutor(
        cancel_source=CancelSource(),
        model_service=model,
        tool_service=tools,
        finalizer=Finalizer(),
    )
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

    assert interrupted["lifecycle"]["status"] == "waiting_agent"
    assert interrupted["lifecycle"]["pending_tool_calls"] == [tool_calls[1]]
    assert tools.calls == [(tool_calls[0],)]

    resumed = await graph.compiled.ainvoke(
        Command(
            resume={
                "resume_type": "agent_result",
                "payload": {"result_summary": "delegated result"},
            }
        ),
        config,
        context=_context(run_id, executor, "command-resume-agent"),
    )

    assert resumed["lifecycle"]["status"] == "completed"
    assert resumed["lifecycle"]["pending_tool_calls"] == []
    assert resumed["lifecycle"]["deferred_resume_messages"] == []
    assert tools.calls == [(tool_calls[0],), (tool_calls[1],)]
    messages = runtime_messages_as_json(cast(RuntimeGraphState, resumed))
    assert [message["role"] for message in messages] == [
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert [
        message["tool_call_id"] for message in messages if message["role"] == "tool"
    ] == ["call-agent", "call-tail"]
    assert "delegated result" in str(messages[-1]["content"])


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
    executor = _executor(model)
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
    waiting = await graph.compiled.aget_state(config)
    assert waiting.next == ("wait",)

    resumed = await graph.compiled.ainvoke(
        Command(
            resume={
                "resume_type": "user_input",
                "payload": {"content": "EXACT RESUME INPUT"},
            }
        ),
        config,
        context=_context(run_id, executor, "command-resume"),
    )

    lifecycle = resumed["lifecycle"]
    assert lifecycle["status"] == "completed"
    assert lifecycle["waiting_request"] is None
    assert "last_applied_command_ids" not in lifecycle
    messages = runtime_messages_as_json(cast(RuntimeGraphState, resumed))
    assert messages[-1]["id"] == str(
        uuid.uuid5(run_id, "resume:command-resume")
    )
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "EXACT RESUME INPUT"
    assert messages[-1]["runtime_input"] == "resume"
    assert messages[-1]["runtime_run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_user_resume_with_pending_tool_returns_to_tool_before_model() -> None:
    run_id = uuid.uuid4()
    tools = ToolService(
        ToolStepResult(
            messages=(
                {
                    "id": "tool-result-1",
                    "role": "tool",
                    "tool_call_id": "call-write-1",
                    "name": "write_file",
                    "content": "The prior write did not take effect.",
                    "execution_status": "failed",
                },
            ),
        )
    )
    executor = _executor(ModelService(), tools=tools)
    state = _state(run_id)
    pending_call: JsonObject = {
        "id": "call-write-1",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"path":"result.md","content":"done"}',
        },
    }
    state["lifecycle"].update(
        {
            "status": "waiting_user",
            "next_route": "wait",
            "pending_tool_calls": [pending_call],
            "waiting_request": {
                "waiting_type": "user",
                "correlation_id": "tool-confirm-1",
            },
        }
    )

    update = await executor.execute(
        "wait",
        state,
        _context(run_id, executor, "command-reconcile"),
        resume_value={
            "resume_type": "user_input",
            "payload": {"content": "The write did not take effect."},
        },
    )

    assert update["lifecycle"]["status"] == "running"
    assert update["lifecycle"]["next_route"] == "tool"
    assert update["lifecycle"]["pending_tool_calls"] == [pending_call]
    assert "messages" not in update
    assert update["lifecycle"]["deferred_resume_messages"][0]["content"] == (
        "The write did not take effect."
    )

    tool_state = cast(
        RuntimeGraphState,
        {**state, "lifecycle": update["lifecycle"]},
    )
    tool_update = await executor.execute(
        "tool",
        tool_state,
        _context(run_id, executor, "command-reconcile"),
    )

    assert [message["role"] for message in tool_update["messages"]] == [
        "tool",
        "user",
    ]
    assert tool_update["lifecycle"]["deferred_resume_messages"] == []


@pytest.mark.asyncio
async def test_external_timer_resume_executes_pending_poll_before_model() -> None:
    run_id = uuid.uuid4()
    poll_call: JsonObject = {
        "id": "async-poll-1",
        "type": "function",
        "function": {
            "name": "download_status",
            "arguments": '{"operation_id":"op-1"}',
        },
    }

    class AsyncPollTools:
        def __init__(self) -> None:
            self.calls: list[tuple[JsonObject, ...]] = []

        async def execute_pending(self, state, context, tool_calls):
            del state, context
            self.calls.append(tool_calls)
            return ToolStepResult(
                messages=(
                    {
                        "id": "poll-result-1",
                        "role": "tool",
                        "tool_call_id": "async-poll-1",
                        "name": "download_status",
                        "content": "download completed",
                        "execution_status": "succeeded",
                        "result_ref": None,
                    },
                )
            )

    model = ModelService(ModelStepResult(intent="finish", finish_content="done"))
    tools = AsyncPollTools()
    executor = _executor(model, tools=tools)
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)
    state = _state(run_id)
    state["messages"] = [
        {
            "id": "poll-proposal-1",
            "role": "assistant",
            "content": "",
            "tool_calls": [poll_call],
        }
    ]
    state["lifecycle"] = {
        "status": "waiting_external",
        "next_route": "wait",
        "pending_tool_calls": [poll_call],
        "waiting_request": {
            "waiting_type": "external",
            "correlation_id": "async-correlation-1",
            "reason": "async_tool_poll_pending",
        },
    }

    interrupted = await graph.compiled.ainvoke(
        state,
        config,
        context=_context(run_id, executor, "command-start"),
    )
    assert interrupted["lifecycle"]["status"] == "waiting_external"
    assert model.calls == 0

    resumed = await graph.compiled.ainvoke(
        Command(
            resume={
                "resume_type": "timer",
                "correlation_id": "async-correlation-1",
                "payload": {"operation_key": "op-1"},
            }
        ),
        config,
        context=_context(run_id, executor, "command-timer"),
    )

    assert tools.calls == [(poll_call,)]
    assert model.calls == 1
    assert resumed["lifecycle"]["status"] == "completed"
    messages = runtime_messages_as_json(cast(RuntimeGraphState, resumed))
    assert not any(message.get("runtime_input") == "resume" for message in messages)


@pytest.mark.asyncio
async def test_external_timer_resume_recovers_legacy_wait_without_pending_call() -> None:
    run_id = uuid.uuid4()
    poll_call: JsonObject = {
        "id": "async-poll-legacy",
        "type": "function",
        "function": {
            "name": "download_status",
            "arguments": '{"operation_id": "op-legacy"}',
        },
    }

    class AsyncPollTools:
        def __init__(self) -> None:
            self.calls: list[tuple[JsonObject, ...]] = []

        async def execute_pending(self, state, context, tool_calls):
            del state, context
            self.calls.append(tool_calls)
            return ToolStepResult(
                messages=(
                    {
                        "id": "poll-result-legacy",
                        "role": "tool",
                        "tool_call_id": "async-poll-legacy",
                        "name": "download_status",
                        "content": "download completed",
                        "execution_status": "succeeded",
                        "result_ref": None,
                    },
                )
            )

    model = ModelService(ModelStepResult(intent="finish", finish_content="done"))
    tools = AsyncPollTools()
    executor = _executor(model, tools=tools)
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)
    state = _state(run_id)
    state["lifecycle"] = {
        "status": "waiting_external",
        "next_route": "wait",
        "pending_tool_calls": [],
        "waiting_request": {
            "waiting_type": "external",
            "correlation_id": f"tool-reconcile:{run_id}",
            "reason": "Tool execution reconciliation is required.",
        },
    }

    await graph.compiled.ainvoke(
        state,
        config,
        context=_context(run_id, executor, "command-start"),
    )
    resumed = await graph.compiled.ainvoke(
        Command(
            resume={
                "resume_type": "timer",
                "correlation_id": f"tool-reconcile:{run_id}",
                "payload": {
                    "operation_key": "op-legacy",
                    "poll_call_id": "async-poll-legacy",
                    "poll": {
                        "tool": "download_status",
                        "arguments": {"operation_id": "op-legacy"},
                    },
                },
            }
        ),
        config,
        context=_context(run_id, executor, "command-timer"),
    )

    assert tools.calls == [(poll_call,)]
    assert model.calls == 1
    assert resumed["lifecycle"]["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_is_observed_before_the_model_or_a_new_tool_can_start() -> None:
    run_id = uuid.uuid4()
    model = ModelService(ModelStepResult(intent="finish", finish_content="too late"))
    cancel = CancelSource(CancelSignal(command_id="cancel-1", reason="user_abort"))
    executor = _executor(model, cancel=cancel)
    graph = build_agent_runtime_graph(
        checkpointer=InMemorySaver(),
        settings=_settings(),
    )
    config = runtime_thread_config(run_id)

    with pytest.raises(RuntimeInvocationCancelled) as raised:
        await graph.compiled.ainvoke(
            _state(run_id),
            config,
            context=_context(run_id, executor, "worker-command"),
        )

    assert raised.value.cancel_command_id == "cancel-1"
    assert raised.value.reason == "user_abort"
    assert model.calls == 0
    preserved = await graph.compiled.aget_state(config)
    assert preserved.values["lifecycle"]["status"] == "running"
    assert "last_applied_command_ids" not in preserved.values["lifecycle"]


@pytest.mark.asyncio
async def test_plain_text_finish_protocol_is_repaired_once_then_fails_explicitly() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="text",
            assistant_message={"role": "assistant", "content": "first plain text"},
            repair_code="missing_finish",
        ),
        ModelStepResult(
            intent="text",
            assistant_message={"role": "assistant", "content": "second plain text"},
            repair_code="missing_finish",
        ),
    )
    executor = _executor(model)

    result = await _invoke(run_id, executor, model_turn_limit=50)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "failed"
    assert lifecycle["reason"] == "finish_protocol_violation"
    assert lifecycle["error"]["code"] == "finish_protocol_violation"
    assert lifecycle["model_step_count"] == 2
    assert lifecycle["model_protocol_repairs"] == {"missing_finish": 1}
    assert model.calls == 2
    messages = runtime_messages_as_json(cast(RuntimeGraphState, result))
    assert [message["role"] for message in messages] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert all(message["runtime_run_id"] == str(run_id) for message in messages)
    assert [message["runtime_intent"] for message in messages] == [
        "repair_draft",
        "repair",
        "repair_draft",
    ]
    assert sum(
        "must either call another available tool" in str(message.get("content", ""))
        for message in messages
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repair_code", "instruction"),
    [
        ("invalid_finish", "Retry finish with valid content."),
        ("invalid_tool_call", "Retry with valid JSON tool arguments."),
    ],
)
async def test_repeated_model_tool_protocol_repair_code_fails_explicitly(
    repair_code: str,
    instruction: str,
) -> None:
    run_id = uuid.uuid4()
    repair = ModelStepResult(
        intent="text",
        assistant_message={"role": "assistant", "content": "bad tool call"},
        repair_instruction=instruction,
        repair_code=repair_code,
    )
    model = ModelService(repair, repair)
    executor = _executor(model)

    result = await _invoke(run_id, executor, model_turn_limit=50)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "failed"
    assert lifecycle["reason"] == "model_tool_protocol_violation"
    assert lifecycle["error"]["code"] == "model_tool_protocol_violation"
    assert lifecycle["model_protocol_repairs"] == {repair_code: 1}
    assert lifecycle["model_step_count"] == 2
    assert model.calls == 2


@pytest.mark.asyncio
async def test_business_repairs_are_not_counted_as_model_tool_protocol_failures() -> None:
    run_id = uuid.uuid4()
    model = ModelService(
        ModelStepResult(
            intent="text",
            repair_instruction="Query current Group members before handoff.",
        ),
        ModelStepResult(
            intent="text",
            repair_instruction="Use an active participant ID.",
        ),
        ModelStepResult(intent="finish", finish_content="Recovered handoff"),
    )
    executor = _executor(model)

    result = await _invoke(run_id, executor, model_turn_limit=50)

    assert result["lifecycle"]["status"] == "completed"
    assert "model_protocol_repairs" not in result["lifecycle"]
    assert model.calls == 3


@pytest.mark.asyncio
async def test_model_turn_limit_is_runtime_context_not_model_visible_input() -> None:
    run_id = uuid.uuid4()
    state = _state(run_id)
    state["snapshots"].initial_input["requested_max_steps"] = 1
    model = ModelService(
        ModelStepResult(
            intent="text",
            assistant_message={"role": "assistant", "content": "first"},
        ),
        ModelStepResult(
            intent="text",
            assistant_message={"role": "assistant", "content": "second"},
        ),
    )
    executor = _executor(model)
    context = RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(run_id),
        command_id="command-budget",
        executor=cast(RuntimeNodeExecutor, executor),
        model_turn_limit=2,
    )

    first = await executor.execute("model", state, context)
    state["lifecycle"] = first["lifecycle"]
    second = await executor.execute("model", state, context)
    state["lifecycle"] = second["lifecycle"]
    exhausted = await executor.execute("model", state, context)

    assert model.calls == 2
    assert exhausted["lifecycle"]["status"] == "failed"
    assert exhausted["lifecycle"]["reason"] == "model_step_limit_reached"
    assert exhausted["lifecycle"]["model_step_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_limit", [None, 0, -1, True])
async def test_missing_or_invalid_model_turn_limit_fails_explicitly(
    invalid_limit: object,
) -> None:
    run_id = uuid.uuid4()
    model = ModelService()
    executor = _executor(model)
    context = RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(run_id),
        command_id="command-invalid-budget",
        executor=cast(RuntimeNodeExecutor, executor),
        model_turn_limit=invalid_limit,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeNodeTransitionError) as raised:
        await executor.execute("model", _state(run_id), context)

    assert raised.value.code == "invalid_model_step_limit"
    assert model.calls == 0


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
    executor = _executor(
        model,
        verifier=verifier,
        max_verification_repairs=1,
    )

    result = await _invoke(run_id, executor)

    lifecycle = result["lifecycle"]
    assert lifecycle["status"] == "failed"
    assert lifecycle["reason"] == "verification_repair_limit_reached"
    assert lifecycle["verification_attempt_count"] == 2
    messages = runtime_messages_as_json(cast(RuntimeGraphState, result))
    assert messages[-1]["id"] == str(
        uuid.uuid5(run_id, "verification:1:repair")
    )
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "add evidence"
    assert verifier.calls == ["first", "second"]
