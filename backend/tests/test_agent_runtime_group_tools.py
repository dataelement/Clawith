"""Current-group tool scope and execution tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections import deque
import json
import uuid

import pytest

from app.models.agent import Agent
from app.models.group import GroupMember
from app.models.participant import Participant
from app.services import group_chat_service, group_file_service
from app.services.agent_runtime import group_runtime_tools
from app.services.agent_runtime.group_runtime_tools import (
    GROUP_TOOL_NAMES,
    GROUP_READ_WORKSPACE_FILE,
    GROUP_WRITE_MEMORY,
    GROUP_WRITE_WORKSPACE_FILE,
    GroupRuntimeToolService,
    with_group_runtime_tools,
)
from app.services.storage_runtime.base import WriteCondition
from app.services.agent_runtime.tool_execution import (
    ToolExecutionError,
    ToolExecutionOutcome,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)


class _Begin:
    def __init__(self, db: "_DB") -> None:
        self.db = db

    async def __aenter__(self):
        self.db.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.db.in_transaction = False
        return False


class _DB:
    def __init__(self) -> None:
        self.in_transaction = False

    def begin(self):
        return _Begin(self)


class _Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return list(self.values)

    def scalars(self):
        return self


class _QueryDB:
    def __init__(self, *values) -> None:
        self.values = deque(values)

    async def execute(self, statement):
        del statement
        return _Rows(self.values.popleft())


def _factory():
    @asynccontextmanager
    async def factory():
        yield _DB()

    return factory


def _state(
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    agent: Agent,
    participant_id: uuid.UUID,
    *,
    group_context: bool,
) -> RuntimeGraphState:
    initial_input = {
        "group_id": str(group_id),
        "target_participant_id": str(participant_id),
    }
    if group_context:
        initial_input["group_context"] = {
            "agent": {"agent_id": str(agent.id)},
        }
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(uuid.uuid4()),
            goal="Use group tools",
            run_kind="foreground",
            source_type="chat",
            model_id=str(uuid.uuid4()),
            graph_name="runtime",
            graph_version="v1",
            agent_id=str(agent.id),
            session_id=str(session_id),
        ),
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input=initial_input,
        ),
        "lifecycle": {"status": "running", "next_route": "tool"},
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
    )


def _agent(tenant_id: uuid.UUID) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Group Agent",
        status="idle",
        is_expired=False,
    )


def test_group_tool_definitions_exist_only_for_validated_group_snapshots() -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    agent = _agent(tenant_id)
    participant_id = uuid.uuid4()
    base = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read from the workspace.",
            },
        }
    ]

    direct_tools = with_group_runtime_tools(
        base,
        _state(
            tenant_id,
            group_id,
            session_id,
            agent,
            participant_id,
            group_context=False,
        ),
    )
    group_tools = with_group_runtime_tools(
        base,
        _state(
            tenant_id,
            group_id,
            session_id,
            agent,
            participant_id,
            group_context=True,
        ),
    )

    assert {tool["function"]["name"] for tool in direct_tools} == {"read_file"}
    assert direct_tools[0]["function"]["description"] == "Read from the workspace."
    assert base[0]["function"]["description"] == "Read from the workspace."
    assert GROUP_TOOL_NAMES.issubset(
        {tool["function"]["name"] for tool in group_tools}
    )
    group_read_file = next(
        tool for tool in group_tools if tool["function"]["name"] == "read_file"
    )
    description = group_read_file["function"]["description"]
    assert "Agent's own Workspace" in description
    assert "not the current Group Workspace" in description
    assert "group_context.workspace_index" in description
    assert "missing result" in description


@pytest.mark.asyncio
async def test_group_memory_tool_uses_checkpoint_group_and_current_agent_only(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    state = _state(
        tenant_id,
        group_id,
        session_id,
        agent,
        participant_id,
        group_context=True,
    )
    calls = []

    async def write_memory(db, **kwargs):
        assert isinstance(db, _DB)
        calls.append(kwargs)
        return group_file_service.GroupTextFile(
            path="memory.md",
            content=kwargs["content"],
            exists=True,
            version_token="v2",
            modified_at="now",
            revision_id=uuid.uuid4(),
        )

    monkeypatch.setattr(group_file_service, "write_agent_memory", write_memory)
    result = await GroupRuntimeToolService(session_factory=_factory()).execute(
        state,
        _context(state),
        agent,
        GROUP_WRITE_MEMORY,
        {
            "content": "remember this",
            "expected_version_token": "v1",
            "agent_id": str(uuid.uuid4()),
        },
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "group_id": group_id,
            "actor_participant_id": participant_id,
            "agent_id": agent.id,
            "content": "remember this",
            "expected_version_token": "v1",
            "session_id": session_id,
        }
    ]
    assert isinstance(result, ToolExecutionOutcome)
    assert result.status == "succeeded"
    receipt = json.loads(result.result_summary or "{}")
    assert receipt["path"] == "memory.md"
    assert "content" not in receipt
    assert receipt["content_hash"]


@pytest.mark.asyncio
async def test_group_member_query_exposes_explicit_agent_id(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    target = _agent(tenant_id)
    target.name = "Researcher"
    target.role_description = "Find reliable evidence"
    participant = Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=target.id,
        display_name="Researcher",
    )
    membership = GroupMember(
        id=uuid.uuid4(),
        group_id=group_id,
        participant_id=participant.id,
        role="member",
    )

    async def authorize(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(group_chat_service, "authorize_group_member", authorize)
    result = await group_runtime_tools._query_members(
        _QueryDB([(membership, participant)], [target]),
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
        query="Researcher",
        participant_type="agent",
        limit=20,
    )

    assert result[0]["participant_id"] == str(participant.id)
    assert result[0]["participant_ref_id"] == str(target.id)
    assert result[0]["agent_id"] == str(target.id)


@pytest.mark.asyncio
async def test_group_text_reads_return_utf8_safe_continuation(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    state = _state(
        tenant_id,
        group_id,
        session_id,
        agent,
        participant_id,
        group_context=True,
    )

    async def read_workspace(db, **kwargs):
        del db, kwargs
        return group_file_service.GroupTextFile(
            path="notes.md",
            content="界界界",
            exists=True,
            version_token="v1",
            modified_at="now",
            revision_id=uuid.uuid4(),
        )

    monkeypatch.setattr(
        group_file_service,
        "read_workspace_file",
        read_workspace,
    )
    service = GroupRuntimeToolService(session_factory=_factory())

    first = await service.execute(
        state,
        _context(state),
        agent,
        GROUP_READ_WORKSPACE_FILE,
        {"path": "notes.md", "max_bytes": 4},
    )
    first_payload = json.loads(first.result_summary or "{}")
    assert first_payload["content"] == "界"
    assert first_payload["has_more"] is True
    assert first_payload["next_offset"] == 3

    second = await service.execute(
        state,
        _context(state),
        agent,
        GROUP_READ_WORKSPACE_FILE,
        {
            "path": "notes.md",
            "offset": first_payload["next_offset"],
            "max_bytes": 6,
        },
    )
    second_payload = json.loads(second.result_summary or "{}")
    assert second_payload["content"] == "界界"
    assert second_payload["has_more"] is False


@pytest.mark.asyncio
async def test_group_workspace_mutation_prepares_applies_and_finalizes_one_operation(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    agent = _agent(tenant_id)
    state = _state(
        tenant_id,
        group_id,
        session_id,
        agent,
        participant_id,
        group_context=True,
    )
    prepared = group_file_service.PreparedRuntimeWorkspaceOperation(
        group_id=group_id,
        operation_id=operation_id,
        revision_id=revision_id,
        operation="write",
        path="report.md",
        storage_key=f"groups/{group_id}/workspace/report.md",
        before_content="draft",
        after_content="final",
        condition=WriteCondition(version_token="v1"),
        content_hash="after-hash",
    )
    receipt = group_file_service.RuntimeWorkspaceOperationReceipt(
        group_id=group_id,
        operation_id=operation_id,
        revision_id=revision_id,
        operation="write",
        path="report.md",
        content_hash="after-hash",
        deleted=False,
    )
    calls: list[tuple[str, object]] = []
    fenced_dbs: list[_DB] = []
    lease_owner = "runtime-invocation-1"

    async def assert_fence(db, **kwargs):
        assert isinstance(db, _DB)
        assert db.in_transaction is True
        fenced_dbs.append(db)
        calls.append(("fence", kwargs))

    async def prepare(db, **kwargs):
        assert isinstance(db, _DB)
        assert db is fenced_dbs[-1]
        assert db.in_transaction is True
        calls.append(("prepare", kwargs))
        return prepared

    async def apply(value):
        assert fenced_dbs[-1].in_transaction is True
        calls.append(("apply", value))

    async def reconcile(db, **kwargs):
        assert isinstance(db, _DB)
        assert db is fenced_dbs[-1]
        assert db.in_transaction is True
        calls.append(("reconcile", kwargs))
        return receipt

    monkeypatch.setattr(
        group_file_service,
        "prepare_runtime_workspace_write",
        prepare,
    )
    monkeypatch.setattr(
        group_file_service,
        "apply_runtime_workspace_operation",
        apply,
    )
    monkeypatch.setattr(
        group_file_service,
        "reconcile_runtime_workspace_operation",
        reconcile,
    )
    monkeypatch.setattr(
        group_runtime_tools,
        "assert_tool_execution_fence",
        assert_fence,
    )

    outcome = await GroupRuntimeToolService(session_factory=_factory()).execute(
        state,
        _context(state),
        agent,
        GROUP_WRITE_WORKSPACE_FILE,
        {"path": "report.md", "content": "final"},
        operation_id=operation_id,
        lease_owner=lease_owner,
    )

    assert [name for name, _ in calls] == [
        "fence",
        "prepare",
        "fence",
        "apply",
        "fence",
        "reconcile",
    ]
    assert calls[0][1] == {
        "tenant_id": tenant_id,
        "execution_id": operation_id,
        "lease_owner": lease_owner,
    }
    assert calls[1][1]["operation_id"] == operation_id
    assert calls[5][1] == {
        "group_id": group_id,
        "operation_id": operation_id,
    }
    assert outcome.status == "succeeded"
    payload = json.loads(outcome.result_summary or "{}")
    assert payload == {
        "content_hash": "after-hash",
        "deleted": False,
        "operation": "write",
        "operation_id": str(operation_id),
        "path": "report.md",
        "revision_id": str(revision_id),
    }
    assert outcome.metadata["operation_id"] == str(operation_id)


@pytest.mark.asyncio
async def test_late_workspace_executor_is_fenced_before_prepare_or_storage(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    agent = _agent(tenant_id)
    state = _state(
        tenant_id,
        group_id,
        session_id,
        agent,
        participant_id,
        group_context=True,
    )

    async def lost_fence(*_args, **_kwargs):
        raise ToolExecutionError(
            "tool_execution_lease_lost",
            "recovery invocation owns the operation",
        )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("lost executor reached Group storage")

    monkeypatch.setattr(
        group_runtime_tools,
        "assert_tool_execution_fence",
        lost_fence,
    )
    monkeypatch.setattr(
        group_file_service,
        "prepare_runtime_workspace_write",
        forbidden,
    )
    monkeypatch.setattr(
        group_file_service,
        "apply_runtime_workspace_operation",
        forbidden,
    )

    with pytest.raises(
        group_runtime_tools.GroupWorkspaceReconciliationPending
    ) as pending:
        await GroupRuntimeToolService(session_factory=_factory()).execute(
            state,
            _context(state),
            agent,
            GROUP_WRITE_WORKSPACE_FILE,
            {"path": "report.md", "content": "late"},
            operation_id=operation_id,
            lease_owner="original-invocation",
        )

    assert pending.value.defer_without_attempt is True


@pytest.mark.asyncio
async def test_takeover_after_prepare_fences_original_before_storage_apply(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    agent = _agent(tenant_id)
    state = _state(
        tenant_id,
        group_id,
        session_id,
        agent,
        participant_id,
        group_context=True,
    )
    prepared = group_file_service.PreparedRuntimeWorkspaceOperation(
        group_id=group_id,
        operation_id=operation_id,
        revision_id=revision_id,
        operation="write",
        path="report.md",
        storage_key=f"groups/{group_id}/workspace/report.md",
        before_content=None,
        after_content="late",
        condition=WriteCondition(require_absent=True),
        content_hash="after-hash",
    )
    fence_checks = 0

    async def assert_fence(*_args, **_kwargs):
        nonlocal fence_checks
        fence_checks += 1
        if fence_checks == 2:
            raise ToolExecutionError(
                "tool_execution_lease_lost",
                "recovery invocation took over",
            )

    async def prepare(*_args, **_kwargs):
        return prepared

    async def forbidden_apply(*_args, **_kwargs):
        raise AssertionError("late original executor repeated storage mutation")

    monkeypatch.setattr(
        group_runtime_tools,
        "assert_tool_execution_fence",
        assert_fence,
    )
    monkeypatch.setattr(
        group_file_service,
        "prepare_runtime_workspace_write",
        prepare,
    )
    monkeypatch.setattr(
        group_file_service,
        "apply_runtime_workspace_operation",
        forbidden_apply,
    )

    with pytest.raises(
        group_runtime_tools.GroupWorkspaceReconciliationPending
    ) as pending:
        await GroupRuntimeToolService(session_factory=_factory()).execute(
            state,
            _context(state),
            agent,
            GROUP_WRITE_WORKSPACE_FILE,
            {"path": "report.md", "content": "late"},
            operation_id=operation_id,
            lease_owner="original-invocation",
        )

    assert pending.value.defer_without_attempt is True
    assert fence_checks == 2
