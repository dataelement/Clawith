"""Receipt-backed Runtime tool-step tests."""

from contextlib import asynccontextmanager
from collections import deque
import uuid

import pytest

from app.models.agent import Agent
from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime import tool_step_service
from app.services.agent_runtime.a2a_runtime import A2ARuntimeToolResult
from app.services.agent_runtime.node_executor import CancelSignal
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.agent_runtime.tool_execution import (
    RetryableToolNodeError,
    ToolExecutionOutcome,
    ToolExecutionReconciliationPending,
    ToolExecutionReservation,
    ToolExecutionTakeover,
    execution_outcome,
)
from app.services.agent_runtime.tool_result_store import ToolResultReconcileResult


class _Result:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _DB:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    async def execute(self, statement):
        del statement
        return _Result(self.agent)

    def begin(self):
        return _Begin()


def _session_factory(agent: Agent):
    @asynccontextmanager
    async def factory():
        yield _DB(agent)

    return factory


class _CancelSource:
    def __init__(self, *signals: CancelSignal | None) -> None:
        self.signals = deque(signals)

    async def get_cancel(self, state, context):
        del state, context
        return self.signals.popleft() if self.signals else None


class _A2AService:
    def __init__(self, result: A2ARuntimeToolResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _ToolResultReconciler:
    def __init__(self, result: ToolResultReconcileResult) -> None:
        self.result = result
        self.calls: list[AgentToolExecution] = []

    async def reconcile_candidate(
        self,
        execution: AgentToolExecution,
    ) -> ToolResultReconcileResult:
        self.calls.append(execution)
        return self.result


def _agent(tenant_id: uuid.UUID, *, access_mode: str = "company") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Tool Agent",
        status="idle",
        is_expired=False,
        access_mode=access_mode,
    )


def _call(call_id: str, name: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _a2a_call(call_id: str, *, mode: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "arguments": (
                '{"agent_name":"Researcher","message":"Check the facts",'
                f'"msg_type":"{mode}"}}'
            ),
        },
    }


def _state(
    tenant_id: uuid.UUID,
    agent: Agent,
    calls: tuple[dict, ...],
    *,
    source_type: str = "chat",
) -> RuntimeGraphState:
    run_id = uuid.uuid4()
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            goal="Use tools",
            run_kind="foreground",
            source_type=source_type,
            model_id=str(uuid.uuid4()),
            graph_name="runtime",
            graph_version="v1",
            agent_id=str(agent.id),
            session_id=str(uuid.uuid4()),
        ),
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "tool",
            "run_messages": [
                {
                    "id": "assistant-message-1",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": list(calls),
                }
            ],
            "pending_tool_calls": list(calls),
        },
    }


def _context(state: RuntimeGraphState) -> RuntimeContext:
    registry = state["registry"]
    return RuntimeContext(
        tenant_id=registry.tenant_id,
        run_id=registry.run_id,
        command_id="command-1",
        executor=object(),  # type: ignore[arg-type]
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
        actor_user_id=str(uuid.uuid4()),
    )


async def _tools(agent_id: uuid.UUID) -> list[dict]:
    del agent_id
    return [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "plaza_get_new_posts"}},
        {"type": "function", "function": {"name": "plaza_create_post"}},
        {"type": "function", "function": {"name": "plaza_add_comment"}},
        {"type": "function", "function": {"name": "send_message_to_agent"}},
        {
            "type": "function",
            "function": {"name": "vercel_get_deploy_logs"},
        },
        {
            "type": "function",
            "function": {"name": "neon_create_database"},
        },
        {
            "type": "function",
            "function": {"name": "vercel_deploy"},
        },
    ]


def _execution(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    call_id: str,
    tool_name: str,
) -> AgentToolExecution:
    return AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=call_id,
        tool_name=tool_name,
        assistant_message_id="assistant-message-1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect=(
            "read"
            if tool_name in {"read_file", "vercel_get_deploy_logs"}
            else "external_write"
        ),
        retry_policy=(
            "safe"
            if tool_name in {"read_file", "vercel_get_deploy_logs"}
            else "never"
        ),
        result_metadata={},
        status="started",
        lease_owner=f"runtime:command-1:{call_id}",
    )


def _reservation(
    execution: AgentToolExecution,
    *,
    reusable: ToolExecutionOutcome | None = None,
    prior_failure: ToolExecutionOutcome | None = None,
    blocked: bool = False,
    requires_confirmation: bool = False,
    error_code: str | None = None,
) -> ToolExecutionReservation:
    return ToolExecutionReservation(
        execution=execution,
        created=not blocked and reusable is None,
        retrying=False,
        reusable_result=reusable,
        prior_failure=prior_failure,
        blocked=blocked,
        reconciliation_required=blocked and prior_failure is None,
        requires_confirmation=requires_confirmation,
        error_code=error_code,
    )


def _service(
    agent: Agent,
    cancel_source: _CancelSource,
    executor,
    *,
    a2a_service=None,
    tool_result_reconciler=None,
) -> tool_step_service.RuntimeToolStepService:
    return tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=cancel_source,
        tool_provider=_tools,
        tool_executor=executor,
        a2a_service=a2a_service,
        tool_result_reconciler=tool_result_reconciler,
    )


@pytest.mark.asyncio
async def test_success_is_reserved_before_execution_and_settled_afterwards(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-1", "read_file")
    state = _state(tenant_id, agent, (call,))
    context = _context(state)
    run_id = context.run_id
    execution = _execution(
        tenant_id,
        uuid.UUID(run_id),
        "call-1",
        "read_file",
    )
    state.pop("registry")
    order = []

    async def reserve(db, **kwargs):
        del db
        order.append(("reserve", kwargs))
        return _reservation(execution)

    async def execute(name, arguments, agent_id, user_id, session_id="", on_output=None):
        del arguments, agent_id, user_id, session_id, on_output
        order.append(("execute", name))
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="file contents",
            result_ref=None,
        )

    async def mark(db, **kwargs):
        del db, kwargs
        order.append(("mark", "succeeded"))
        execution.status = "succeeded"
        execution.result_summary = "file contents"
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_succeeded", mark)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        context,
        (call,),
    )

    assert [item[0] for item in order] == ["reserve", "execute", "mark"]
    assert order[0][1]["side_effect_classification"] == "read"
    assert order[0][1]["retry_policy"] == "safe"
    assert result.error is None
    assert result.waiting_request is None
    assert result.pending_tool_calls == ()
    assert result.messages == (
        {
            "id": str(
                uuid.uuid5(
                    uuid.UUID(run_id),
                    "tool-result:call-1",
                )
            ),
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": "file contents",
            "execution_status": "succeeded",
            "result_ref": None,
        },
    )


@pytest.mark.asyncio
async def test_async_pending_interrupts_with_a_deterministic_poll_call(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-async", "read_file")
    state = _state(tenant_id, agent, (call,))
    context = _context(state)
    execution = _execution(
        tenant_id,
        uuid.UUID(context.run_id),
        "call-async",
        "read_file",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="pending",
            result_summary="Download is still pending; poll again.",
            result_ref=None,
            metadata={
                "runtime_async_pending": True,
                "async_operation": {
                    "version": 1,
                    "operation_key": "operation-key",
                    "operation_id": "2501.01234",
                    "state": "downloading",
                    "poll": {
                        "tool": "read_file",
                        "arguments": {"paper_id": "2501.01234"},
                        "interval_ms": 1000,
                    },
                },
            },
        )

    async def mark_pending(db, **kwargs):
        del db
        assert kwargs["metadata"]["runtime_async_pending"] is True
        assert kwargs["metadata"]["async_poll_scheduled"] is False
        settled_execution = _execution(
            tenant_id,
            uuid.UUID(context.run_id),
            "call-async",
            "read_file",
        )
        settled_execution.id = execution.id
        settled_execution.result_summary = kwargs["result_summary"]
        settled_execution.result_metadata = kwargs["metadata"]
        settled_execution.lease_owner = None
        return settled_execution

    async def terminal_forbidden(*args, **kwargs):
        raise AssertionError(f"pending operation was closed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_async_pending",
        mark_pending,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        terminal_forbidden,
    )

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        context,
        (call,),
    )

    # The reservation object remains stale because settlement used another
    # session; Runtime must build the poll interrupt from the settled outcome.
    assert execution.result_metadata == {}
    assert execution.status == "started"
    assert execution.lease_owner == "runtime:command-1:call-async"
    assert result.waiting_request == {
        "waiting_type": "external",
        "correlation_id": str(
            uuid.uuid5(uuid.UUID(context.run_id), f"async-poll:{execution.id}")
        ),
        "reason": "async_tool_poll_pending",
        "tool_call_id": "call-async",
        "operation_key": "operation-key",
    }
    assert len(result.pending_tool_calls) == 1
    poll_call = result.pending_tool_calls[0]
    assert poll_call == {
        "id": f"async-poll:{execution.id}",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": '{"paper_id": "2501.01234"}',
        },
    }
    assert result.messages[0]["execution_status"] == "pending"
    assert result.messages[1]["role"] == "assistant"
    assert result.messages[1]["tool_calls"] == [poll_call]


@pytest.mark.asyncio
async def test_terminal_async_poll_settles_same_run_operation(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-poll", "read_file")
    state = _state(tenant_id, agent, (call,))
    context = _context(state)
    execution = _execution(
        tenant_id,
        uuid.UUID(context.run_id),
        "call-poll",
        "read_file",
    )
    settle_calls: list[dict] = []

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Download completed.",
            result_ref=None,
            metadata={
                "runtime_async_pending": False,
                "async_operation": {
                    "version": 1,
                    "operation_key": "operation-key",
                    "operation_id": "2501.01234",
                    "state": "success",
                    "poll": {
                        "tool": "read_file",
                        "arguments": {"paper_id": "2501.01234"},
                        "interval_ms": 1000,
                    },
                },
            },
        )

    async def settle_async(db, **kwargs):
        del db
        settle_calls.append(kwargs)
        execution.status = kwargs["status"]
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def ordinary_settle_forbidden(*args, **kwargs):
        raise AssertionError(f"async poll used ordinary settlement: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "settle_async_operation_executions",
        settle_async,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        ordinary_settle_forbidden,
    )

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        context,
        (call,),
    )

    assert len(settle_calls) == 1
    assert settle_calls[0]["run_id"] == uuid.UUID(context.run_id)
    assert settle_calls[0]["metadata"]["runtime_async_pending"] is False
    assert result.messages[0]["execution_status"] == "succeeded"


@pytest.mark.asyncio
async def test_unknown_async_poll_settles_operation_before_waiting_for_reconciliation(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-poll-unknown", "read_file")
    state = _state(tenant_id, agent, (call,))
    context = _context(state)
    execution = _execution(
        tenant_id,
        uuid.UUID(context.run_id),
        "call-poll-unknown",
        "read_file",
    )
    settle_calls: list[dict] = []

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="unknown",
            result_summary="Poll response was ambiguous.",
            result_ref=None,
            error_code="mcp_async_protocol_conflict",
            metadata={
                "runtime_async_pending": False,
                "async_operation": {
                    "version": 1,
                    "operation_key": "operation-key",
                    "operation_id": "2501.01234",
                    "state": "unknown",
                    "poll": {
                        "tool": "read_file",
                        "arguments": {"paper_id": "2501.01234"},
                        "interval_ms": 1000,
                    },
                },
            },
        )

    async def settle_async(db, **kwargs):
        del db
        settle_calls.append(kwargs)
        execution.status = kwargs["status"]
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "settle_async_operation_executions",
        settle_async,
    )

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        context,
        (call,),
    )

    assert settle_calls[0]["status"] == "unknown"
    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected_status", "expects_wait"),
    [
        ("read_file", "failed", False),
        ("write_file", "unknown", True),
    ],
)
async def test_untyped_string_outcomes_fail_closed(
    monkeypatch,
    tool_name: str,
    expected_status: str,
    expects_wait: bool,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-untyped", tool_name)
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-untyped",
        tool_name,
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return "legacy display string"

    async def settle(db, **kwargs):
        del db
        execution.status = expected_status
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed"
        if expected_status == "failed"
        else "mark_tool_execution_unknown",
        settle,
    )

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.error is None
    assert bool(result.waiting_request) is expects_wait
    if expects_wait:
        assert result.messages == ()
        assert result.pending_tool_calls == (call,)
        assert result.waiting_request["reason"] == "untyped_tool_outcome"
    else:
        assert result.messages[0]["execution_status"] == "failed"
        assert result.messages[0]["error_code"] == "untyped_tool_outcome"


@pytest.mark.asyncio
async def test_large_typed_result_is_archived_before_ledger_settlement(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-large", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-large",
        "read_file",
    )
    order: list[str] = []

    async def reserve(db, **kwargs):
        del db, kwargs
        order.append("reserve")
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        order.append("execute")
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="界" * 100,
            result_ref=None,
        )

    class _ResultStore:
        async def write(self, execution_arg, outcome, content):
            assert execution_arg is execution
            assert outcome.status == "succeeded"
            assert len(content.encode("utf-8")) > 32
            order.append("archive")
            return f"tool-result://{execution.id}"

    async def mark(db, **kwargs):
        del db
        order.append("mark")
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_succeeded", mark)
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=execute,
        tool_result_store=_ResultStore(),  # type: ignore[arg-type]
    )
    service._inline_result_max_bytes = 32

    context = _context(state)
    result = await service.execute_pending(state, context, (call,))

    assert order == ["reserve", "execute", "archive", "mark"]
    assert result.messages[0]["result_ref"] == f"tool-result://{execution.id}"
    assert len(result.messages[0]["content"].encode("utf-8")) <= 32
    assert execution.result_metadata["archive_status"] == "stored"


@pytest.mark.asyncio
async def test_large_vercel_logs_are_archived_and_replayed_without_reexecution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-vercel-logs", "vercel_get_deploy_logs")
    call["function"]["arguments"] = '{"deployment_id":"dpl-large"}'
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-vercel-logs",
        "vercel_get_deploy_logs",
    )
    provider_calls = 0
    reserve_calls = 0
    archive_calls = 0

    async def reserve(db, **kwargs):
        nonlocal reserve_calls
        del db, kwargs
        reserve_calls += 1
        if reserve_calls == 1:
            return _reservation(execution)
        reusable = ToolExecutionOutcome(
            status="succeeded",
            result_summary=execution.result_summary,
            result_ref=execution.result_ref,
            evidence_refs=("vercel-deployment://dpl-large",),
            metadata=execution.result_metadata,
        )
        return _reservation(execution, reusable=reusable)

    async def execute(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        if provider_calls > 1:
            raise AssertionError("replayed Vercel read reached the provider")
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary=("large Vercel log line\n" * 1000),
            result_ref=None,
            evidence_refs=("vercel-deployment://dpl-large",),
        )

    class _ResultStore:
        async def write(self, execution_arg, outcome, content):
            nonlocal archive_calls
            assert execution_arg is execution
            assert outcome.result_ref is None
            assert outcome.evidence_refs == (
                "vercel-deployment://dpl-large",
            )
            assert len(content.encode("utf-8")) > 8192
            archive_calls += 1
            return f"tool-result://{execution.id}"

    async def mark(db, **kwargs):
        del db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None, None),
        tool_provider=_tools,
        tool_executor=execute,
        tool_result_store=_ResultStore(),  # type: ignore[arg-type]
    )
    service._inline_result_max_bytes = 8192
    context = _context(state)

    first = await service.execute_pending(state, context, (call,))
    replay = await service.execute_pending(state, context, (call,))

    expected_ref = f"tool-result://{execution.id}"
    assert first.messages[0]["result_ref"] == expected_ref
    assert replay.messages[0]["result_ref"] == expected_ref
    assert provider_calls == 1
    assert archive_calls == 1
    assert reserve_calls == 2
    assert execution.result_metadata["archive_status"] == "stored"


@pytest.mark.asyncio
async def test_neon_private_value_ref_is_settled_and_replayed_without_secret(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-neon-create", "neon_create_database")
    call["function"]["arguments"] = (
        '{"project_name":"analytics","database_name":"warehouse"}'
    )
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-neon-create",
        "neon_create_database",
    )
    value_ref = f"deploy-value://{tenant_id}/{agent.id}/value-1"
    connection_uri = "postgresql://user:private@db.example/warehouse"
    execute_calls = 0
    reserve_calls = 0

    async def reserve(db, **kwargs):
        nonlocal reserve_calls
        del db, kwargs
        reserve_calls += 1
        if reserve_calls == 1:
            return _reservation(execution)
        return _reservation(
            execution,
            reusable=execution_outcome(execution),
        )

    async def execute(*args, **kwargs):
        nonlocal execute_calls
        del args, kwargs
        execute_calls += 1
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Neon project project-1 created with a private value ref.",
            result_ref="project-1",
            evidence_refs=("neon-project://project-1",),
            metadata={
                "provider": "neon",
                "operation": "project_create",
                "project_id": "project-1",
                "database_name": "warehouse",
                "value_ref": value_ref,
                "provider_payload": connection_uri,
            },
        )

    async def mark(db, **kwargs):
        del db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark,
    )
    service = _service(agent, _CancelSource(None, None), execute)
    context = _context(state)

    first = await service.execute_pending(state, context, (call,))
    replay = await service.execute_pending(state, context, (call,))

    assert first.messages[0]["result_ref"] == "project-1"
    assert replay.messages[0]["result_ref"] == "project-1"
    assert execute_calls == 1
    assert reserve_calls == 2
    assert execution.result_metadata["value_ref"] == value_ref
    assert "provider_payload" not in execution.result_metadata
    assert connection_uri not in repr(execution.result_metadata)


@pytest.mark.asyncio
async def test_vercel_deploy_receipts_are_settled_and_replayed_without_reexecution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-vercel-deploy", "vercel_deploy")
    call["function"]["arguments"] = (
        '{"project_name":"app","deploy_method":"github",'
        '"github_repo":"owner/repo","git_ref":"main"}'
    )
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-vercel-deploy",
        "vercel_deploy",
    )
    execute_calls = 0
    reserve_calls = 0

    async def reserve(db, **kwargs):
        nonlocal reserve_calls
        del db, kwargs
        reserve_calls += 1
        if reserve_calls == 1:
            return _reservation(execution)
        return _reservation(
            execution,
            reusable=execution_outcome(execution),
        )

    async def execute(*args, **kwargs):
        nonlocal execute_calls
        del args, kwargs
        execute_calls += 1
        if execute_calls > 1:
            raise AssertionError("replayed Vercel deploy reached the provider")
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Vercel deployment deployment-1 is READY.",
            result_ref="deployment-1",
            artifact_refs=("https://app-abc.vercel.app",),
            evidence_refs=("vercel-deployment://deployment-1",),
            metadata={
                "provider": "vercel",
                "operation": "deployment_accepted",
                "project_id": "project-1",
                "project_name": "app",
                "deploy_method": "github",
                "git_ref": "main",
                "linked_repo": "owner/repo",
                "confirmed_blob_digests": [],
                "deployment_id": "deployment-1",
                "deployment_url": "https://app-abc.vercel.app",
                "deployment_state": "READY",
                "provider_payload": "must-not-persist",
            },
        )

    async def mark(db, **kwargs):
        del db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark,
    )
    service = _service(agent, _CancelSource(None, None), execute)
    context = _context(state)

    first = await service.execute_pending(state, context, (call,))
    replay = await service.execute_pending(state, context, (call,))

    assert first.messages[0]["result_ref"] == "deployment-1"
    assert replay.messages[0]["result_ref"] == "deployment-1"
    assert execute_calls == 1
    assert reserve_calls == 2
    assert execution.result_metadata["project_id"] == "project-1"
    assert execution.result_metadata["linked_repo"] == "owner/repo"
    assert execution.result_metadata["deployment_id"] == "deployment-1"
    assert execution.result_metadata["deployment_state"] == "READY"
    assert execution.result_metadata["artifact_refs"] == [
        "https://app-abc.vercel.app"
    ]
    assert execution.result_metadata["evidence_refs"] == [
        "vercel-deployment://deployment-1"
    ]
    assert "provider_payload" not in execution.result_metadata


@pytest.mark.asyncio
async def test_archive_success_with_ledger_settlement_failure_keeps_started_receipt(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-settle-fail", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-settle-fail",
        "read_file",
    )
    order: list[str] = []

    async def reserve(db, **kwargs):
        del db, kwargs
        order.append("reserve")
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        order.append("execute")
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="x" * 100,
            result_ref=None,
        )

    class _ResultStore:
        async def write(self, execution_arg, outcome, content):
            assert execution_arg is execution
            assert outcome.status == "succeeded"
            assert content == "x" * 100
            order.append("archive")
            return f"tool-result://{execution.id}"

    async def fail_settlement(db, **kwargs):
        del db, kwargs
        order.append("settle")
        raise RuntimeError("database settlement failed")

    async def forbidden_failure_settlement(*args, **kwargs):
        raise AssertionError(
            f"settlement failure was rewritten as a tool outcome: {args}, {kwargs}"
        )

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        fail_settlement,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed",
        forbidden_failure_settlement,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=execute,
        tool_result_store=_ResultStore(),  # type: ignore[arg-type]
    )
    service._inline_result_max_bytes = 16

    result = await service.execute_pending(state, _context(state), (call,))

    assert order == ["reserve", "execute", "archive", "settle"]
    assert execution.status == "started"
    assert result.messages == ()
    assert result.waiting_request is None
    assert result.error == {
        "code": "tool_execution_failed",
        "message": "Runtime tool step failed: RuntimeError",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected_status", "expected_retryable"),
    [
        ("read_file", "failed", False),
        ("write_file", "succeeded", False),
    ],
)
async def test_archive_failure_never_turns_a_confirmed_write_into_unknown(
    monkeypatch,
    tool_name: str,
    expected_status: str,
    expected_retryable: bool,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-archive-fail", tool_name)
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-archive-fail",
        tool_name,
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="x" * 100,
            result_ref=None,
        )

    class _FailingResultStore:
        async def write(self, execution_arg, outcome, content):
            del execution_arg, outcome, content
            raise OSError("storage unavailable")

    async def settle(db, **kwargs):
        del db
        execution.status = expected_status
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed"
        if expected_status == "failed"
        else "mark_tool_execution_succeeded",
        settle,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=execute,
        tool_result_store=_FailingResultStore(),  # type: ignore[arg-type]
    )
    service._inline_result_max_bytes = 16

    result = await service.execute_pending(state, _context(state), (call,))

    assert result.error is None
    assert result.waiting_request is None
    assert result.messages[0]["execution_status"] == expected_status
    assert result.messages[0].get("retryable", False) is expected_retryable
    assert execution.result_metadata["archive_status"] == "failed"
    if tool_name == "read_file":
        assert result.messages[0]["error_code"] == "tool_result_archive_failed"
    else:
        assert execution.result_metadata["archive_error_code"] == "OSError"


@pytest.mark.asyncio
async def test_group_write_tool_uses_checkpoint_scoped_executor_and_conditional_policy(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = {
        "id": "call-group-write",
        "type": "function",
        "function": {
            "name": "group_write_memory",
            "arguments": '{"content":"remember"}',
        },
    }
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"group_context": {"agent": {"agent_id": str(agent.id)}}},
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-write",
        "group_write_memory",
    )
    reserved = []

    async def reserve(db, **kwargs):
        del db
        reserved.append(kwargs)
        return _reservation(execution)

    async def mark(db, **kwargs):
        del db, kwargs
        execution.status = "succeeded"
        execution.result_summary = '{"path":"memory.md"}'
        return execution

    async def generic_executor(*_args, **_kwargs):
        raise AssertionError("group tools must not use the Agent workspace executor")

    class _GroupToolService:
        def __init__(self) -> None:
            self.calls = []

        async def execute(
            self,
            state_arg,
            context_arg,
            agent_arg,
            tool_name,
            arguments,
        ):
            self.calls.append(
                (state_arg, context_arg, agent_arg, tool_name, arguments)
            )
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary='{"path":"memory.md"}',
                result_ref=None,
            )

    group_tools = _GroupToolService()
    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_succeeded", mark)
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=generic_executor,
        group_tool_service=group_tools,  # type: ignore[arg-type]
    )

    context = _context(state)
    result = await service.execute_pending(state, context, (call,))

    assert result.error is None
    assert reserved[0]["side_effect_classification"] == "write"
    assert reserved[0]["retry_policy"] == "conditional"
    assert group_tools.calls[0][1] is context
    assert group_tools.calls[0][2] is agent
    assert group_tools.calls[0][3:] == (
        "group_write_memory",
        {"content": "remember"},
    )


@pytest.mark.asyncio
async def test_group_workspace_write_uses_ledger_id_and_reconciles_without_reexecution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = {
        "id": "call-group-workspace-write",
        "type": "function",
        "function": {
            "name": "group_write_workspace_file",
            "arguments": '{"path":"report.md","content":"final"}',
        },
    }
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "group_id": str(uuid.uuid4()),
            "target_participant_id": str(uuid.uuid4()),
            "group_context": {"agent": {"agent_id": str(agent.id)}},
        },
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-workspace-write",
        "group_write_workspace_file",
    )
    execution.effect = "write"
    execution.retry_policy = "conditional"
    reservations = deque(
        [
            _reservation(execution),
            _reservation(
                execution,
                blocked=True,
                error_code="tool_execution_started",
            ),
        ]
    )
    settled: list[dict] = []

    async def reserve(db, **_kwargs):
        del db
        return reservations.popleft()

    async def mark(db, **kwargs):
        del db
        settled.append(kwargs)
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    class _GroupToolService:
        def __init__(self) -> None:
            self.execute_operation_ids: list[tuple[uuid.UUID, str]] = []
            self.reconcile_operation_ids: list[tuple[uuid.UUID, str]] = []

        async def execute(
            self,
            _state,
            _context,
            _agent,
            _tool_name,
            _arguments,
            *,
            operation_id,
            lease_owner,
        ):
            self.execute_operation_ids.append((operation_id, lease_owner))
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary=(
                    '{"content_hash":"hash","operation":"write",'
                    f'"operation_id":"{operation_id}","path":"report.md",'
                    '"revision_id":"revision-1"}'
                ),
                result_ref=None,
                metadata={"operation_id": str(operation_id)},
            )

        async def reconcile_workspace_operation(
            self,
            _state,
            _context,
            _agent,
            _tool_name,
            _arguments,
            *,
            operation_id,
            lease_owner,
        ):
            self.reconcile_operation_ids.append((operation_id, lease_owner))
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary=(
                    '{"content_hash":"hash","operation":"write",'
                    f'"operation_id":"{operation_id}","path":"report.md",'
                    '"revision_id":"revision-1"}'
                ),
                result_ref=None,
                metadata={"operation_id": str(operation_id)},
            )

    group_tools = _GroupToolService()

    async def takeover(db, **kwargs):
        del db
        execution.lease_owner = kwargs["lease_owner"]
        return ToolExecutionTakeover(
            execution=execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
        )

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "takeover_tool_execution_for_reconciliation",
        takeover,
    )
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_succeeded", mark)
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None, None),
        tool_provider=_tools,
        tool_executor=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        group_tool_service=group_tools,  # type: ignore[arg-type]
    )

    first = await service.execute_pending(state, _context(state), (call,))
    execution.status = "started"
    second = await service.execute_pending(state, _context(state), (call,))

    assert first.error is None
    assert second.error is None
    assert len(group_tools.execute_operation_ids) == 1
    assert group_tools.execute_operation_ids[0][0] == execution.id
    assert len(group_tools.reconcile_operation_ids) == 1
    assert group_tools.reconcile_operation_ids[0][0] == execution.id
    assert group_tools.execute_operation_ids[0][1] != (
        group_tools.reconcile_operation_ids[0][1]
    )
    assert settled[0]["execution_id"] == execution.id
    assert settled[1]["execution_id"] == execution.id
    assert settled[1]["lease_owner"] == execution.lease_owner
    assert second.messages[0]["content"] == first.messages[0]["content"]


@pytest.mark.asyncio
async def test_active_group_workspace_lease_defers_without_reconcile_or_settle(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = {
        "id": "call-group-workspace-active",
        "type": "function",
        "function": {
            "name": "group_write_workspace_file",
            "arguments": '{"path":"report.md","content":"final"}',
        },
    }
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "group_id": str(uuid.uuid4()),
            "target_participant_id": str(uuid.uuid4()),
            "group_context": {"agent": {"agent_id": str(agent.id)}},
        },
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-workspace-active",
        "group_write_workspace_file",
    )
    execution.effect = "write"
    execution.retry_policy = "conditional"

    async def reserve(db, **_kwargs):
        del db
        return _reservation(
            execution,
            blocked=True,
            error_code="tool_execution_started",
        )

    async def active_takeover(db, **_kwargs):
        del db
        return ToolExecutionTakeover(
            execution=execution,
            acquired=False,
            active=True,
            terminal_outcome=None,
        )

    async def forbidden_settle(*_args, **_kwargs):
        raise AssertionError("active lease was settled by another invocation")

    class _GroupToolService:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("active lease re-executed storage")

        async def reconcile_workspace_operation(self, *_args, **_kwargs):
            raise AssertionError("active lease was reconciled")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "takeover_tool_execution_for_reconciliation",
        active_takeover,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        forbidden_settle,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        group_tool_service=_GroupToolService(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        tool_step_service.GroupWorkspaceReconciliationPending
    ) as pending:
        await service.execute_pending(state, _context(state), (call,))

    assert pending.value.defer_without_attempt is True


def test_reinvoked_command_after_thread_lock_loss_gets_a_distinct_fence_owner() -> None:
    first = tool_step_service._tool_execution_lease_owner("command-1", "call-1")
    second = tool_step_service._tool_execution_lease_owner("command-1", "call-1")

    assert first != second
    assert first.startswith("runtime:command-1:call-1:")
    assert second.startswith("runtime:command-1:call-1:")
    assert len(first) <= 128
    assert len(second) <= 128


@pytest.mark.asyncio
async def test_group_workspace_ledger_settlement_failure_stays_reconcilable(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = {
        "id": "call-group-workspace-settle-failure",
        "type": "function",
        "function": {
            "name": "group_delete_workspace_file",
            "arguments": '{"path":"obsolete.md"}',
        },
    }
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "group_id": str(uuid.uuid4()),
            "target_participant_id": str(uuid.uuid4()),
            "group_context": {"agent": {"agent_id": str(agent.id)}},
        },
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-workspace-settle-failure",
        "group_delete_workspace_file",
    )
    execution.effect = "write"
    execution.retry_policy = "conditional"

    async def reserve(db, **_kwargs):
        del db
        return _reservation(execution)

    async def fail_settle(*_args, **_kwargs):
        raise OSError("database unavailable after storage success")

    class _GroupToolService:
        async def execute(self, *_args, operation_id, **_kwargs):
            return ToolExecutionOutcome(
                status="succeeded",
                result_summary=(
                    '{"deleted":true,"operation":"delete",'
                    f'"operation_id":"{operation_id}","path":"obsolete.md",'
                    '"revision_id":"revision-1"}'
                ),
                result_ref=None,
                metadata={"operation_id": str(operation_id)},
            )

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        fail_settle,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        group_tool_service=_GroupToolService(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        tool_step_service.GroupWorkspaceReconciliationPending
    ):
        await service.execute_pending(state, _context(state), (call,))

    assert execution.status == "started"


@pytest.mark.asyncio
async def test_group_workspace_unproven_replay_settles_unknown_without_reexecution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = {
        "id": "call-group-workspace-conflict",
        "type": "function",
        "function": {
            "name": "group_write_workspace_file",
            "arguments": '{"path":"report.md","content":"expected"}',
        },
    }
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "group_id": str(uuid.uuid4()),
            "target_participant_id": str(uuid.uuid4()),
            "group_context": {"agent": {"agent_id": str(agent.id)}},
        },
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-workspace-conflict",
        "group_write_workspace_file",
    )
    execution.effect = "write"
    execution.retry_policy = "conditional"

    async def reserve(db, **_kwargs):
        del db
        return _reservation(
            execution,
            blocked=True,
            error_code="tool_execution_started",
        )

    async def mark_unknown(db, **kwargs):
        del db
        execution.status = "unknown"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def takeover(db, **kwargs):
        del db
        execution.lease_owner = kwargs["lease_owner"]
        return ToolExecutionTakeover(
            execution=execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
        )

    class _GroupToolService:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("reconciliation must not execute storage again")

        async def reconcile_workspace_operation(
            self,
            *_args,
            operation_id,
            **_kwargs,
        ):
            return ToolExecutionOutcome(
                status="unknown",
                result_summary="Current storage does not match the prepared after hash",
                result_ref=None,
                error_code="group_workspace_reconciliation_conflict",
                metadata={"operation_id": str(operation_id)},
            )

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "takeover_tool_execution_for_reconciliation",
        takeover,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_unknown",
        mark_unknown,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=_tools,
        tool_executor=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        group_tool_service=_GroupToolService(),  # type: ignore[arg-type]
    )

    result = await service.execute_pending(state, _context(state), (call,))

    assert execution.status == "unknown"
    assert result.waiting_request is None
    assert result.error == {
        "code": "group_workspace_reconciliation_conflict",
        "message": "Current storage does not match the prepared after hash",
    }
    assert result.messages[0]["execution_status"] == "unknown"


@pytest.mark.asyncio
async def test_group_preflight_confirmation_is_typed_failure_for_public_finish(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-group-confirm", "write_file")
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"group_context": {"agent": {"agent_id": str(agent.id)}}},
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-confirm",
        "write_file",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="failed",
            result_summary="Please confirm the exact destination before writing.",
            result_ref=None,
            error_code="confirmation_required",
        )

    async def mark_failed(db, **kwargs):
        del db
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_failed", mark_failed)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.error is None
    assert result.waiting_request is None
    assert result.pending_tool_calls == ()
    assert result.messages[0]["execution_status"] == "failed"
    assert result.messages[0]["error_code"] == "confirmation_required"
    assert "exact destination" in result.messages[0]["content"]


@pytest.mark.asyncio
async def test_group_unknown_outcome_fails_run_without_user_interrupt(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    first = _call("call-group-unknown", "write_file")
    second = _call("call-group-after", "read_file")
    state = _state(tenant_id, agent, (first, second))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"group_context": {"agent": {"agent_id": str(agent.id)}}},
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-unknown",
        "write_file",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="unknown",
            result_summary="Provider disconnected after accepting the request.",
            result_ref=None,
            error_code="provider_outcome_unknown",
        )

    async def mark_unknown(db, **kwargs):
        del db
        execution.status = "unknown"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_unknown", mark_unknown)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (first, second),
    )

    assert execution.status == "unknown"
    assert result.waiting_request is None
    assert result.pending_tool_calls == (second,)
    assert result.messages[0]["execution_status"] == "unknown"
    assert result.messages[0]["error_code"] == "provider_outcome_unknown"
    assert result.error == {
        "code": "provider_outcome_unknown",
        "message": "Provider disconnected after accepting the request.",
    }


@pytest.mark.asyncio
async def test_succeeded_receipt_is_reused_without_executing_tool(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-reuse", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-reuse",
        "read_file",
    )
    reusable = ToolExecutionOutcome(
        status="succeeded",
        result_summary="cached result",
        result_ref=None,
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution, reusable=reusable)

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"reused tool executed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    result = await _service(agent, _CancelSource(None), forbidden).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.messages[0]["content"] == "cached result"
    assert result.messages[0]["execution_status"] == "succeeded"


@pytest.mark.asyncio
async def test_read_failure_is_known_and_returned_to_model_without_retry(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-fail", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-fail",
        "read_file",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError("secret path")

    async def mark_failed(db, **kwargs):
        del db
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_failed", mark_failed)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.error is None
    assert result.waiting_request is None
    assert result.messages[0]["execution_status"] == "failed"
    assert result.messages[0]["content"] == "FileNotFoundError: tool execution failed"
    assert "secret path" not in str(result.messages[0])


@pytest.mark.asyncio
async def test_retryable_read_failure_retries_same_receipt_then_returns_one_result(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-retry", "read_file")
    state = _state(tenant_id, agent, (call,))
    context = _context(state)
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-retry",
        "read_file",
    )
    execution.attempt_count = 1
    provider_calls = 0
    reserve_calls: list[bool] = []

    async def reserve(db, **kwargs):
        del db
        reserve_calls.append(kwargs["resume_safe_read"])
        if len(reserve_calls) == 2:
            execution.attempt_count = 2
        return _reservation(execution)

    async def execute(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        if provider_calls == 1:
            return ToolExecutionOutcome(
                status="failed",
                result_summary="Temporary read failure.",
                result_ref=None,
                error_code="temporary_read_failure",
                retryable=True,
            )
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Recovered contents.",
            result_ref=None,
        )

    async def mark_retry_pending(db, **kwargs):
        del db
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def mark_succeeded(db, **kwargs):
        del db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_retry_pending",
        mark_retry_pending,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark_succeeded,
    )
    service = _service(agent, _CancelSource(None, None), execute)

    with pytest.raises(RetryableToolNodeError):
        await service.execute_pending(state, context, (call,))
    result = await service.execute_pending(state, context, (call,))

    assert provider_calls == 2
    assert reserve_calls == [True, True]
    assert len(result.messages) == 1
    assert result.messages[0]["tool_call_id"] == "call-read-retry"
    assert result.messages[0]["execution_status"] == "succeeded"
    assert result.messages[0]["content"] == "Recovered contents."
    assert execution.result_metadata["runtime_attempt_count"] == 2


@pytest.mark.asyncio
async def test_retryable_read_exhaustion_returns_one_non_retryable_result(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-exhausted", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-exhausted",
        "read_file",
    )
    execution.attempt_count = 3

    async def reserve(db, **kwargs):
        del db
        assert kwargs["resume_safe_read"] is True
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="failed",
            result_summary="Temporary read failure.",
            result_ref=None,
            error_code="temporary_read_failure",
            retryable=True,
        )

    async def mark_failed(db, **kwargs):
        del db
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_failed", mark_failed)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert len(result.messages) == 1
    assert result.messages[0]["execution_status"] == "failed"
    assert result.messages[0]["error_code"] == "tool_retry_exhausted"
    assert result.messages[0].get("retryable") is None
    assert "Do not repeat the identical tool call unchanged" in result.messages[0][
        "content"
    ]
    assert execution.result_metadata["runtime_attempt_count"] == 3
    assert execution.result_metadata["runtime_retry_exhausted"] is True
    assert execution.result_metadata["last_error_code"] == "temporary_read_failure"


@pytest.mark.asyncio
async def test_write_exception_is_unknown_and_preserves_the_unresolved_batch(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    first = _call("call-write", "write_file")
    second = _call("call-after", "read_file")
    state = _state(tenant_id, agent, (first, second))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-write",
        "write_file",
    )

    async def reserve(db, **kwargs):
        del db
        assert kwargs["side_effect_classification"] == "write"
        assert kwargs["retry_policy"] == "conditional"
        return _reservation(execution)

    async def execute(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("outcome unknown")

    async def mark_unknown(db, **kwargs):
        del db
        execution.status = "unknown"
        execution.result_summary = kwargs["result_summary"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_unknown", mark_unknown)

    result = await _service(agent, _CancelSource(None), execute).execute_pending(
        state,
        _context(state),
        (first, second),
    )

    assert result.messages == ()
    assert result.error is None
    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "user"
    assert result.waiting_request["reason"] == "tool_outcome_unknown"
    assert result.pending_tool_calls == (first, second)


@pytest.mark.asyncio
async def test_cancel_between_calls_stops_before_reserving_the_next_tool(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    first = _call("call-first", "read_file")
    second = _call("call-second", "read_file")
    state = _state(tenant_id, agent, (first, second))
    reserved = []

    async def reserve(db, **kwargs):
        del db
        reserved.append(kwargs["tool_call_id"])
        return _reservation(
            _execution(
                tenant_id,
                uuid.UUID(state["registry"].run_id),
                kwargs["tool_call_id"],
                kwargs["tool_name"],
            )
        )

    async def execute(*args, **kwargs):
        del args, kwargs
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="done",
            result_ref=None,
        )

    async def mark(db, **kwargs):
        del db
        execution = _execution(
            tenant_id,
            uuid.UUID(state["registry"].run_id),
            "call-first",
            "read_file",
        )
        execution.id = kwargs["execution_id"]
        execution.status = "succeeded"
        execution.result_summary = "done"
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_succeeded", mark)
    cancel = CancelSignal(command_id="cancel-command", reason="user_abort")

    result = await _service(
        agent,
        _CancelSource(None, cancel),
        execute,
    ).execute_pending(state, _context(state), (first, second))

    assert reserved == ["call-first"]
    assert len(result.messages) == 1
    assert result.cancel_signal == cancel
    assert result.pending_tool_calls == ()


@pytest.mark.asyncio
async def test_started_receipt_waits_for_reconciliation_and_keeps_pending_call(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-started", "write_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-started",
        "write_file",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="tool_execution_started",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"started tool executed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    result = await _service(agent, _CancelSource(None), forbidden).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "external"
    assert result.pending_tool_calls == (call,)


@pytest.mark.asyncio
async def test_active_safe_read_receipt_defers_command_without_provider_replay(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-active", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-active",
        "read_file",
    )
    execution.attempt_count = 2

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="tool_execution_started",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"active safe read was replayed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    with pytest.raises(ToolExecutionReconciliationPending) as exc_info:
        await _service(
            agent,
            _CancelSource(None),
            forbidden,
        ).execute_pending(state, _context(state), (call,))

    assert exc_info.value.code == "safe_read_attempt_active"
    assert exc_info.value.defer_without_attempt is True


@pytest.mark.asyncio
async def test_expired_safe_read_recovers_archived_success_before_closing(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-reconcile", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-reconcile",
        "read_file",
    )
    recovered = ToolExecutionOutcome(
        status="succeeded",
        result_summary="archived read result",
        result_ref=f"tool-result://{execution.id}",
    )
    reconciler = _ToolResultReconciler(
        ToolResultReconcileResult(
            status="reconciled",
            execution_id=execution.id,
            outcome=recovered,
        )
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="safe_read_result_reconciliation_required",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"reconciled safe read was replayed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    result = await _service(
        agent,
        _CancelSource(None),
        forbidden,
        tool_result_reconciler=reconciler,
    ).execute_pending(state, _context(state), (call,))

    assert reconciler.calls == [execution]
    assert len(result.messages) == 1
    assert result.messages[0]["tool_call_id"] == "call-read-reconcile"
    assert result.messages[0]["content"] == "archived read result"
    assert result.waiting_request is None


@pytest.mark.asyncio
async def test_expired_safe_read_closes_only_after_store_probe_misses(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-missing", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-missing",
        "read_file",
    )
    reconciler = _ToolResultReconciler(
        ToolResultReconcileResult(
            status="unavailable",
            execution_id=execution.id,
            error_code="tool_result_unreadable",
        )
    )
    closed = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-missing",
        "read_file",
    )
    closed.id = execution.id
    closed.status = "failed"
    closed.result_summary = "safe read result unavailable"
    closed.result_metadata = {
        "error_code": "safe_read_result_unavailable",
        "retryable": False,
    }
    close_calls = []

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="safe_read_result_reconciliation_required",
        )

    async def close(db, **kwargs):
        del db
        close_calls.append(kwargs)
        return closed

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"missing safe read was replayed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_expired_safe_read_result_unavailable",
        close,
    )

    result = await _service(
        agent,
        _CancelSource(None),
        forbidden,
        tool_result_reconciler=reconciler,
    ).execute_pending(state, _context(state), (call,))

    assert reconciler.calls == [execution]
    assert close_calls == [
        {
            "tenant_id": tenant_id,
            "execution_id": execution.id,
            "probe_error_code": "tool_result_unreadable",
        }
    ]
    assert len(result.messages) == 1
    assert result.messages[0]["error_code"] == "safe_read_result_unavailable"
    assert result.waiting_request is None


@pytest.mark.asyncio
async def test_expired_safe_read_defers_on_transient_store_probe_failure(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-probe-timeout", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-probe-timeout",
        "read_file",
    )
    reconciler = _ToolResultReconciler(
        ToolResultReconcileResult(
            status="deferred",
            execution_id=execution.id,
            error_code="tool_result_probe_failed",
        )
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="safe_read_result_reconciliation_required",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"deferred safe read was replayed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    with pytest.raises(ToolExecutionReconciliationPending) as exc_info:
        await _service(
            agent,
            _CancelSource(None),
            forbidden,
            tool_result_reconciler=reconciler,
        ).execute_pending(state, _context(state), (call,))

    assert reconciler.calls == [execution]
    assert exc_info.value.code == "safe_read_result_reconciliation_pending"
    assert exc_info.value.defer_without_attempt is True


@pytest.mark.asyncio
async def test_expired_safe_read_defers_when_unavailable_close_fails(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-read-close-timeout", "read_file")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-read-close-timeout",
        "read_file",
    )
    reconciler = _ToolResultReconciler(
        ToolResultReconcileResult(
            status="unavailable",
            execution_id=execution.id,
            error_code="tool_result_unreadable",
        )
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            error_code="safe_read_result_reconciliation_required",
        )

    async def close(db, **kwargs):
        del db, kwargs
        raise TimeoutError("ledger close timed out")

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"unsettled safe read was replayed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_expired_safe_read_result_unavailable",
        close,
    )

    with pytest.raises(ToolExecutionReconciliationPending) as exc_info:
        await _service(
            agent,
            _CancelSource(None),
            forbidden,
            tool_result_reconciler=reconciler,
        ).execute_pending(state, _context(state), (call,))

    assert exc_info.value.code == "safe_read_result_reconciliation_pending"
    assert exc_info.value.defer_without_attempt is True
    assert execution.status == "started"


@pytest.mark.asyncio
async def test_group_unknown_receipt_fails_without_user_interrupt_or_reexecution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("call-group-unknown-replay", "write_file")
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"group_context": {"agent": {"agent_id": str(agent.id)}}},
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "call-group-unknown-replay",
        "write_file",
    )
    execution.status = "unknown"
    execution.result_summary = "The provider accepted the request but no receipt arrived."
    execution.result_metadata = {
        "error_code": "provider_outcome_unknown",
        "retryable": False,
    }

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(
            execution,
            blocked=True,
            requires_confirmation=True,
            error_code="tool_outcome_unknown",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"unknown Group tool was re-executed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)

    result = await _service(agent, _CancelSource(None), forbidden).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert execution.status == "unknown"
    assert result.waiting_request is None
    assert result.pending_tool_calls == ()
    assert result.messages[0]["execution_status"] == "unknown"
    assert result.error == {
        "code": "provider_outcome_unknown",
        "message": "The provider accepted the request but no receipt arrived.",
    }


@pytest.mark.asyncio
async def test_duplicate_call_ids_fail_before_any_reservation_or_execution() -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    first = _call("duplicate", "read_file")
    second = _call("duplicate", "write_file")
    state = _state(tenant_id, agent, (first, second))

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"duplicate call executed: {args}, {kwargs}")

    result = await _service(
        agent,
        _CancelSource(),
        forbidden,
    ).execute_pending(state, _context(state), (first, second))

    assert result.error is not None
    assert result.error["code"] == "invalid_tool_call"
    assert result.messages == ()


@pytest.mark.asyncio
async def test_private_heartbeat_plaza_call_is_receipted_without_execution(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id, access_mode="private")
    call = _call("private-plaza", "plaza_get_new_posts")
    state = _state(tenant_id, agent, (call,), source_type="heartbeat")
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "private-plaza",
        "plaza_get_new_posts",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def mark_failed(db, **kwargs):
        del db
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        return execution

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"private Plaza tool executed: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed",
        mark_failed,
    )

    result = await _service(agent, _CancelSource(None), forbidden).execute_pending(
        state,
        _context(state),
        (call,),
    )

    assert result.error is None
    assert result.messages[0]["execution_status"] == "failed"
    assert result.messages[0]["content"] == (
        "[BLOCKED] Private heartbeat Agents cannot use Agent Plaza."
    )


@pytest.mark.asyncio
async def test_public_heartbeat_comment_limit_counts_successful_receipts(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    calls = tuple(
        _call(f"comment-{index}", "plaza_add_comment")
        for index in range(1, 4)
    )
    state = _state(tenant_id, agent, calls, source_type="heartbeat")
    run_id = uuid.UUID(state["registry"].run_id)
    executions: dict[uuid.UUID, AgentToolExecution] = {}
    successful_counts = deque([0, 1, 2])
    executed: list[str] = []

    async def reserve(db, **kwargs):
        del db
        execution = _execution(
            tenant_id,
            run_id,
            kwargs["tool_call_id"],
            kwargs["tool_name"],
        )
        executions[execution.id] = execution
        return _reservation(execution)

    async def successful_count(**kwargs):
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["run_id"] == run_id
        assert kwargs["tool_name"] == "plaza_add_comment"
        return successful_counts.popleft()

    async def execute(name, arguments, agent_id, user_id, session_id="", on_output=None):
        del arguments, agent_id, user_id, session_id, on_output
        executed.append(name)
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="comment added",
            result_ref=None,
        )

    async def mark_succeeded(db, **kwargs):
        del db
        execution = executions[kwargs["execution_id"]]
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        return execution

    async def mark_failed(db, **kwargs):
        del db
        execution = executions[kwargs["execution_id"]]
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark_succeeded,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed",
        mark_failed,
    )
    service = _service(agent, _CancelSource(None, None, None), execute)
    monkeypatch.setattr(service, "_successful_tool_count", successful_count)

    result = await service.execute_pending(state, _context(state), calls)

    assert executed == ["plaza_add_comment", "plaza_add_comment"]
    assert [message["execution_status"] for message in result.messages] == [
        "succeeded",
        "succeeded",
        "failed",
    ]
    assert result.messages[2]["content"] == (
        "[BLOCKED] Heartbeat limit reached for plaza_add_comment (maximum 2)."
    )


@pytest.mark.asyncio
async def test_replayed_heartbeat_plaza_call_reuses_receipt_before_limit_check(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call("replayed-post", "plaza_create_post")
    state = _state(tenant_id, agent, (call,), source_type="heartbeat")
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "replayed-post",
        "plaza_create_post",
    )
    reusable = ToolExecutionOutcome(
        status="succeeded",
        result_summary="original post",
        result_ref=None,
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution, reusable=reusable)

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"replayed Plaza tool executed: {args}, {kwargs}")

    async def forbidden_count(**kwargs):
        raise AssertionError(f"replayed receipt was counted again: {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    service = _service(agent, _CancelSource(None), forbidden)
    monkeypatch.setattr(service, "_successful_tool_count", forbidden_count)

    result = await service.execute_pending(state, _context(state), (call,))

    assert result.messages[0]["execution_status"] == "succeeded"
    assert result.messages[0]["content"] == "original post"


@pytest.mark.asyncio
async def test_runtime_a2a_request_interrupts_source_after_durable_target_acceptance(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _a2a_call("delegate-1", mode="task_delegate")
    state = _state(tenant_id, agent, (call,))
    run_id = uuid.UUID(state["registry"].run_id)
    execution = _execution(
        tenant_id,
        run_id,
        "delegate-1",
        "send_message_to_agent",
    )
    target_run_id = uuid.uuid4()
    correlation_id = f"a2a:task_delegate:{uuid.uuid4()}"
    a2a_service = _A2AService(
        A2ARuntimeToolResult(
            outcome=ToolExecutionOutcome(
                status="succeeded",
                result_summary="delegation accepted",
                result_ref=f"agent-run:{target_run_id}",
            ),
            target_run_id=target_run_id,
            waiting_request={
                "waiting_type": "agent",
                "correlation_id": correlation_id,
                "reason": "waiting_for_task_delegate",
                "target_run_id": str(target_run_id),
            },
        )
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"legacy A2A executor called: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    result = await _service(
        agent,
        _CancelSource(None),
        forbidden,
        a2a_service=a2a_service,
    ).execute_pending(state, _context(state), (call,))

    assert len(a2a_service.calls) == 1
    assert a2a_service.calls[0]["source_run_id"] == run_id
    assert result.error is None
    assert result.waiting_request is not None
    assert result.waiting_request["waiting_type"] == "agent"
    assert result.waiting_request["correlation_id"] == correlation_id
    assert result.pending_tool_calls == ()
    assert result.messages[0]["execution_status"] == "succeeded"
    assert result.messages[0]["result_ref"] == f"agent-run:{target_run_id}"


@pytest.mark.asyncio
async def test_replayed_runtime_a2a_request_rebuilds_same_interrupt_from_receipt(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _a2a_call("consult-1", mode="consult")
    state = _state(tenant_id, agent, (call,))
    run_id = uuid.UUID(state["registry"].run_id)
    target_run_id = uuid.uuid4()
    execution = _execution(
        tenant_id,
        run_id,
        "consult-1",
        "send_message_to_agent",
    )
    reusable = ToolExecutionOutcome(
        status="succeeded",
        result_summary="consultation accepted",
        result_ref=f"agent-run:{target_run_id}",
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution, reusable=reusable)

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"replayed A2A executed: {args}, {kwargs}")

    a2a_service = _A2AService(
        A2ARuntimeToolResult(
            outcome=reusable,
            target_run_id=target_run_id,
        )
    )
    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    result = await _service(
        agent,
        _CancelSource(None),
        forbidden,
        a2a_service=a2a_service,
    ).execute_pending(state, _context(state), (call,))

    assert a2a_service.calls == []
    assert result.waiting_request == {
        "waiting_type": "agent",
        "correlation_id": (
            f"a2a:consult:{uuid.uuid5(run_id, 'a2a-result:consult-1')}"
        ),
        "reason": "waiting_for_consult",
        "target_run_id": str(target_run_id),
    }
    assert result.messages[0]["content"] == "consultation accepted"


@pytest.mark.asyncio
async def test_runtime_a2a_notify_continues_without_waiting(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _a2a_call("notify-1", mode="notify")
    state = _state(tenant_id, agent, (call,))
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        "notify-1",
        "send_message_to_agent",
    )
    target_run_id = uuid.uuid4()
    a2a_service = _A2AService(
        A2ARuntimeToolResult(
            outcome=ToolExecutionOutcome(
                status="succeeded",
                result_summary="notification accepted",
                result_ref=f"agent-run:{target_run_id}",
            ),
            target_run_id=target_run_id,
        )
    )

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"legacy notify executor called: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    result = await _service(
        agent,
        _CancelSource(None),
        forbidden,
        a2a_service=a2a_service,
    ).execute_pending(state, _context(state), (call,))

    assert result.waiting_request is None
    assert result.pending_tool_calls == ()
    assert result.messages[0]["content"] == "notification accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    (
        "send_channel_message",
        "send_platform_message",
        "send_feishu_message",
        "send_channel_file",
        "send_file_to_agent",
    ),
)
async def test_group_cross_space_aliases_fail_before_provider_dispatch(
    monkeypatch,
    tool_name: str,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call(f"blocked-{tool_name}", tool_name)
    state = _state(tenant_id, agent, (call,))
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"group_context": {"group_id": str(uuid.uuid4())}},
    )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        f"blocked-{tool_name}",
        tool_name,
    )

    async def tools(agent_id):
        assert agent_id == agent.id
        return [{"type": "function", "function": {"name": tool_name}}]

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def mark_failed(db, **kwargs):
        del db
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        execution.error_code = kwargs["error_code"]
        return execution

    async def forbidden(*args, **kwargs):
        raise AssertionError(f"cross-space provider was called: {args}, {kwargs}")

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(tool_step_service, "mark_tool_execution_failed", mark_failed)
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=tools,
        tool_executor=forbidden,
    )

    result = await service.execute_pending(state, _context(state), (call,))

    assert result.error is None
    assert result.waiting_request is None
    assert result.messages[0]["execution_status"] == "failed"
    assert result.messages[0]["error_code"] == (
        "group_cross_space_confirmation_required"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_group", "tool_name"),
    (
        (False, "send_channel_message"),
        (True, "send_message_to_agent"),
    ),
)
async def test_group_cross_space_policy_does_not_change_other_tool_paths(
    monkeypatch,
    is_group: bool,
    tool_name: str,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    call = _call(f"allowed-{tool_name}", tool_name)
    state = _state(tenant_id, agent, (call,))
    if is_group:
        state["snapshots"] = RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={"group_context": {"group_id": str(uuid.uuid4())}},
        )
    execution = _execution(
        tenant_id,
        uuid.UUID(state["registry"].run_id),
        f"allowed-{tool_name}",
        tool_name,
    )
    dispatched: list[str] = []

    async def tools(agent_id):
        assert agent_id == agent.id
        return [{"type": "function", "function": {"name": tool_name}}]

    async def reserve(db, **kwargs):
        del db, kwargs
        return _reservation(execution)

    async def execute(name, arguments, agent_id, user_id, session_id="", on_output=None):
        del arguments, agent_id, user_id, session_id, on_output
        dispatched.append(name)
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="sent",
            result_ref=None,
        )

    async def mark_succeeded(db, **kwargs):
        del db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        return execution

    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reserve)
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark_succeeded,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=_session_factory(agent),
        cancel_source=_CancelSource(None),
        tool_provider=tools,
        tool_executor=execute,
    )

    result = await service.execute_pending(state, _context(state), (call,))

    assert result.error is None
    assert dispatched == [tool_name]
    assert result.messages[0]["execution_status"] == "succeeded"
