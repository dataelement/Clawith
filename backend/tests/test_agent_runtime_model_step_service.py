"""Runtime model-step adapter tests."""

from contextlib import asynccontextmanager
import uuid

import pytest

from app.models.agent import Agent
from app.models.llm import LLMModel
from app.services.agent_runtime.context_builder import RuntimeContextBuild
from app.services.agent_runtime.model_step_service import RuntimeModelStepService
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.token_tracker import TokenUsage


class _Result:
    def __init__(self, values=None) -> None:
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _DB:
    def __init__(self, model: LLMModel, agent: Agent) -> None:
        self.results = iter((_Result([model]), _Result([agent]), _Result()))

    async def execute(self, statement):
        del statement
        return next(self.results)


def _session_factory(model: LLMModel, agent: Agent):
    @asynccontextmanager
    async def factory():
        yield _DB(model, agent)

    return factory


def _failover_session_factory(
    model: LLMModel,
    agent: Agent,
    fallback: LLMModel,
):
    calls = 0

    @asynccontextmanager
    async def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _DB(model, agent)
            return

        class _FallbackDB:
            async def execute(self, statement):
                del statement
                return _Result([fallback])

        yield _FallbackDB()

    return factory


class _ContextBuilder:
    def __init__(self, build: RuntimeContextBuild) -> None:
        self.build_result = build
        self.calls = []

    async def build(self, state, **kwargs):
        del state
        self.calls.append(kwargs)
        return self.build_result


def _model(tenant_id: uuid.UUID, *, capable: bool = True) -> LLMModel:
    return LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="runtime-model",
        api_key_encrypted="encrypted",
        label="Runtime Model",
        enabled=True,
        supports_vision=False,
        max_output_tokens=2048,
        max_input_tokens=100_000 if capable else None,
        context_window_tokens=None,
    )


def _agent(tenant_id: uuid.UUID) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Runtime Agent",
        role_description="Solve the task",
        status="idle",
        is_expired=False,
    )


def _state(
    tenant_id: uuid.UUID,
    model: LLMModel,
    agent: Agent,
) -> RuntimeGraphState:
    run_id = uuid.uuid4()
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            goal="Answer the request",
            run_kind="foreground",
            source_type="chat",
            model_id=str(model.id),
            graph_name="runtime",
            graph_version="v1",
            agent_id=str(agent.id),
            session_id=str(uuid.uuid4()),
        ),
        "snapshots": RunInputSnapshots(
            session_context={"version": 1, "summary": "shared"},
            session_context_version=1,
            recent_session_messages=(
                {
                    "id": "session-message-1",
                    "role": "user",
                    "content": "Please inspect the file",
                },
            ),
            related_run_summaries=(),
            initial_input={"message_id": "session-message-1"},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "model",
            "run_messages": [],
            "pending_tool_calls": [],
        },
    }


def _build(**overrides) -> RuntimeContextBuild:
    values = {
        "session_context_snapshot": {"version": 1, "summary": "shared"},
        "current_run": {"goal": "Answer the request"},
        "related_run_summaries": (),
        "pending_session_messages_snapshot": (
            {
                "id": "pending-session-message-1",
                "role": "assistant",
                "content": "Earlier decision from the pending compact zone",
            },
        ),
        "recent_session_messages_snapshot": (
            {
                "id": "session-message-1",
                "role": "user",
                "content": "Please inspect the file",
            },
        ),
        "recent_run_messages": (),
        "initial_input": {"message_id": "session-message-1"},
        "resume_input": None,
        "omitted_tool_exchanges": (),
        "retry_model": False,
        "blocked": False,
        "requires_confirmation": False,
    }
    values.update(overrides)
    return RuntimeContextBuild(**values)


async def _tools(agent_id: uuid.UUID) -> list[dict]:
    del agent_id
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def _prompt(*args, **kwargs) -> tuple[str, str]:
    del args, kwargs
    return "Static role", "Dynamic context"


def _context(state: RuntimeGraphState) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=state["registry"].tenant_id,
        run_id=state["registry"].run_id,
        command_id="command-1",
        executor=object(),  # type: ignore[arg-type]
    )


def _service(
    model: LLMModel,
    agent: Agent,
    builder: _ContextBuilder,
    completion,
) -> RuntimeModelStepService:
    return RuntimeModelStepService(
        session_factory=_session_factory(model, agent),
        context_builder=builder,  # type: ignore[arg-type]
        completion=completion,
        tool_provider=_tools,
        prompt_builder=_prompt,
    )


def _failover_service(
    model: LLMModel,
    fallback: LLMModel,
    agent: Agent,
    builder: _ContextBuilder,
    completion,
) -> RuntimeModelStepService:
    return RuntimeModelStepService(
        session_factory=_failover_session_factory(model, agent, fallback),
        context_builder=builder,  # type: ignore[arg-type]
        completion=completion,
        tool_provider=_tools,
        prompt_builder=_prompt,
    )


@pytest.mark.asyncio
async def test_normal_tool_proposal_is_stable_and_does_not_execute_in_model_step() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    builder = _ContextBuilder(_build())
    calls = []

    async def complete(model_arg, messages, **kwargs):
        calls.append((model_arg, messages, kwargs))
        return LLMCompletionStep(
            content="",
            tool_calls=(
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"notes.md"}',
                    },
                },
            ),
            reasoning_content="inspect",
            retry_instruction=None,
            usage=TokenUsage(total_tokens=20),
        )

    result = await _service(model, agent, builder, complete).complete_once(
        state,
        _context(state),
    )

    expected_message_id = str(uuid.uuid5(uuid.UUID(state["registry"].run_id), "model-step:1:assistant"))
    assert result.intent == "tool_calls"
    assert result.assistant_message is not None
    assert result.assistant_message["id"] == expected_message_id
    assert result.assistant_message["tool_calls"] == list(result.tool_calls)
    assert result.assistant_message["reasoning_content"] == "inspect"
    assert len(calls) == 1
    tool_names = {tool["function"]["name"] for tool in calls[0][2]["tools"]}
    assert tool_names == {"read_file", "finish", "wait"}
    assert calls[0][1][0].role == "system"
    assert "Earlier decision from the pending compact zone" in calls[0][1][0].dynamic_content
    assert calls[0][1][1].role == "user"
    assert calls[0][1][1].content == "Please inspect the file"
    assert len(builder.calls) == 2
    assert builder.calls[1]["run_message_token_budget"] > 0


@pytest.mark.asyncio
async def test_current_input_uses_executable_content_and_trusted_runtime_instruction() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    state["snapshots"] = RunInputSnapshots(
        session_context=state["snapshots"].session_context,
        session_context_version=state["snapshots"].session_context_version,
        recent_session_messages=(
            {
                "id": "session-message-1",
                "role": "user",
                "content": "Visible question",
            },
        ),
        related_run_summaries=(),
        initial_input={
            "message_id": "session-message-1",
            "input_content": "Executable question with workspace evidence",
            "runtime_instruction": "Begin the trusted onboarding flow.",
        },
    )
    builder = _ContextBuilder(
        _build(
            recent_session_messages_snapshot=state["snapshots"].recent_session_messages,
            initial_input=state["snapshots"].initial_input,
        )
    )
    calls = []

    async def complete(_model, messages, **kwargs):
        calls.append((messages, kwargs))
        return LLMCompletionStep(
            content="Done",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=10),
        )

    result = await _service(model, agent, builder, complete).complete_once(
        state,
        _context(state),
    )

    assert result.intent == "text"
    assert calls[0][0][1].content == "Executable question with workspace evidence"
    assert "Begin the trusted onboarding flow." in calls[0][0][0].dynamic_content


@pytest.mark.asyncio
async def test_user_resume_envelope_is_rendered_as_plain_user_input() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    resume_message = {
        "id": "resume-message-1",
        "role": "user",
        "content": {
            "resume_type": "user_input",
            "correlation_id": "confirm-7",
            "payload": {
                "message_id": "session-message-2",
                "content": "Yes, continue",
            },
        },
        "runtime_input": "resume",
    }
    state["lifecycle"]["run_messages"] = [resume_message]
    builder = _ContextBuilder(_build(recent_run_messages=(resume_message,)))
    calls = []

    async def complete(_model, messages, **kwargs):
        calls.append((messages, kwargs))
        return LLMCompletionStep(
            content="Continuing",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=10),
        )

    result = await _service(model, agent, builder, complete).complete_once(
        state,
        _context(state),
    )

    assert result.intent == "text"
    assert calls[0][0][-1].role == "user"
    assert calls[0][0][-1].content == "Yes, continue"


@pytest.mark.asyncio
async def test_synthetic_input_is_injected_without_enabling_agent_tools() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    state["snapshots"] = RunInputSnapshots(
        session_context=state["snapshots"].session_context,
        session_context_version=state["snapshots"].session_context_version,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "message_id": "synthetic-message-1",
            "input_content": "Please begin onboarding.",
            "application_tools_enabled": False,
        },
    )
    builder = _ContextBuilder(
        _build(
            recent_session_messages_snapshot=(),
            initial_input=state["snapshots"].initial_input,
        )
    )
    calls = []

    async def complete(_model, messages, **kwargs):
        calls.append((messages, kwargs))
        return LLMCompletionStep(
            content="",
            tool_calls=(
                {
                    "id": "finish-1",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": '{"content":"Welcome"}',
                    },
                },
            ),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=10),
        )

    result = await _service(model, agent, builder, complete).complete_once(
        state,
        _context(state),
    )

    assert result.intent == "finish"
    assert calls[0][0][1].content == "Please begin onboarding."
    assert {tool["function"]["name"] for tool in calls[0][1]["tools"]} == {
        "finish",
        "wait",
    }


@pytest.mark.asyncio
async def test_group_snapshot_adds_only_current_group_tools_and_platform_rules() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 1, "summary": "shared"},
        session_context_version=1,
        recent_session_messages=state["snapshots"].recent_session_messages,
        related_run_summaries=(),
        initial_input={"group_context": {"group": {"group_id": str(uuid.uuid4())}}},
    )
    builder = _ContextBuilder(_build(initial_input=state["snapshots"].initial_input))
    calls = []

    async def complete(_model, messages, **kwargs):
        calls.append((messages, kwargs))
        return LLMCompletionStep(
            content="Group reply",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=20),
        )

    result = await _service(model, agent, builder, complete).complete_once(
        state,
        _context(state),
    )

    assert result.intent == "text"
    tool_names = {tool["function"]["name"] for tool in calls[0][1]["tools"]}
    assert {
        "group_query_members",
        "group_read_announcement",
        "group_read_memory",
        "group_write_memory",
        "group_list_workspace",
        "group_read_workspace_file",
        "group_write_workspace_file",
        "group_delete_workspace_file",
    }.issubset(tool_names)
    assert "Answer only from this group" in calls[0][0][0].dynamic_content


@pytest.mark.asyncio
async def test_finish_is_a_control_intent_not_an_unpaired_tool_exchange() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)

    async def complete(*args, **kwargs):
        del args, kwargs
        return LLMCompletionStep(
            content="",
            tool_calls=(
                {
                    "id": "finish-1",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": '{"content":"Final answer"}',
                    },
                },
            ),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=10),
        )

    result = await _service(
        model,
        agent,
        _ContextBuilder(_build()),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "finish"
    assert result.finish_content == "Final answer"
    assert result.assistant_message is not None
    assert "tool_calls" not in result.assistant_message
    assert result.assistant_message["runtime_intent"] == "finish"
    assert result.assistant_message["content"] == "Final answer"


@pytest.mark.asyncio
async def test_wait_uses_a_runtime_generated_correlation_id() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)

    async def complete(*args, **kwargs):
        del args, kwargs
        return LLMCompletionStep(
            content="Need confirmation",
            tool_calls=(
                {
                    "id": "wait-1",
                    "type": "function",
                    "function": {
                        "name": "wait",
                        "arguments": ('{"waiting_type":"user","reason":"Need approval","question":"Continue?"}'),
                    },
                },
            ),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=10),
        )

    result = await _service(
        model,
        agent,
        _ContextBuilder(_build()),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "wait"
    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "user"
    assert result.waiting_request["reason"] == "Need approval"
    assert result.waiting_request["question"] == "Continue?"
    assert result.waiting_request["correlation_id"] == str(
        uuid.uuid5(uuid.UUID(state["registry"].run_id), "model-step:1:wait")
    )


@pytest.mark.asyncio
async def test_mixed_finish_and_tool_calls_are_repaired_before_any_tool_runs() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)

    async def complete(*args, **kwargs):
        del args, kwargs
        return LLMCompletionStep(
            content="",
            tool_calls=(
                {
                    "id": "finish-1",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": '{"content":"Done"}',
                    },
                },
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
            ),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await _service(
        model,
        agent,
        _ContextBuilder(_build()),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "text"
    assert result.tool_calls == ()
    assert result.repair_instruction is not None
    assert "only tool call" in result.repair_instruction
    assert result.assistant_message is not None
    assert "tool_calls" not in result.assistant_message


@pytest.mark.asyncio
async def test_unknown_model_budget_does_not_block_provider_call() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id, capable=False)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    called = False

    async def complete(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        return LLMCompletionStep(
            content="Ready",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    result = await _service(
        model,
        agent,
        _ContextBuilder(_build()),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "text"
    assert result.error is None
    assert result.assistant_message is not None
    assert result.assistant_message["content"] == "Ready"
    assert called is True


@pytest.mark.asyncio
async def test_unknown_tool_outcome_waits_for_reconciliation_without_calling_model() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    agent = _agent(tenant_id)
    state = _state(tenant_id, model, agent)
    called = False

    async def complete(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("provider must not be called")

    result = await _service(
        model,
        agent,
        _ContextBuilder(_build(blocked=True)),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "wait"
    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "external"
    assert str(result.waiting_request["correlation_id"]).startswith("tool-reconcile:")
    assert called is False


@pytest.mark.asyncio
async def test_retryable_primary_error_rebuilds_budget_for_fallback_once() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    fallback = _model(tenant_id)
    fallback.model = "fallback-model"
    fallback.max_input_tokens = 20_000
    agent = _agent(tenant_id)
    agent.fallback_model_id = fallback.id
    state = _state(tenant_id, model, agent)
    builder = _ContextBuilder(_build())
    called_models: list[uuid.UUID] = []

    async def complete(model_arg, *args, **kwargs):
        del args, kwargs
        called_models.append(model_arg.id)
        if model_arg.id == model.id:
            raise TimeoutError("provider timeout")
        return LLMCompletionStep(
            content="",
            tool_calls=(
                {
                    "id": "finish-fallback",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": '{"content":"Fallback answer"}',
                    },
                },
            ),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(total_tokens=12),
        )

    result = await _failover_service(
        model,
        fallback,
        agent,
        builder,
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "finish"
    assert result.finish_content == "Fallback answer"
    assert called_models == [model.id, fallback.id]
    assert len(builder.calls) == 4
    primary_budget = builder.calls[1]["run_message_token_budget"]
    fallback_budget = builder.calls[3]["run_message_token_budget"]
    assert fallback_budget < primary_budget
    assert result.assistant_message is not None
    assert result.assistant_message["runtime_model_id"] == str(fallback.id)
    assert result.assistant_message["runtime_failover_from_model_id"] == str(model.id)


@pytest.mark.asyncio
async def test_non_retryable_primary_error_never_calls_configured_fallback() -> None:
    tenant_id = uuid.uuid4()
    model = _model(tenant_id)
    fallback = _model(tenant_id)
    agent = _agent(tenant_id)
    agent.fallback_model_id = fallback.id
    state = _state(tenant_id, model, agent)
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        raise RuntimeError("invalid API key")

    result = await _failover_service(
        model,
        fallback,
        agent,
        _ContextBuilder(_build()),
        complete,
    ).complete_once(state, _context(state))

    assert result.intent == "error"
    assert result.error is not None
    assert result.error["code"] == "model_call_failed"
    assert calls == 1
