"""Current-group tool scope and execution tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import uuid

import pytest

from app.database import add_after_commit_callback, add_after_rollback_callback
from app.models.agent import Agent
from app.services import group_file_service
from app.services.agent_runtime.group_runtime_tools import (
    GROUP_TOOL_NAMES,
    GROUP_WRITE_MEMORY,
    GROUP_WRITE_WORKSPACE_FILE,
    GroupRuntimeToolService,
    with_group_runtime_tools,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _DB:
    def __init__(self) -> None:
        self.info = {}
        self.events: list[str] = []

    def begin(self):
        return _Begin()

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


def _factory(db: _DB | None = None):
    session = db or _DB()

    @asynccontextmanager
    async def factory():
        yield session

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
        {"type": "function", "function": {"name": "list_files"}},
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "jina_search"}},
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

    assert {tool["function"]["name"] for tool in direct_tools} == {
        "list_files",
        "read_file",
        "write_file",
        "jina_search",
    }
    group_tool_names = {tool["function"]["name"] for tool in group_tools}
    assert GROUP_TOOL_NAMES.issubset(group_tool_names)
    assert "jina_search" in group_tool_names
    assert {"list_files", "read_file", "write_file"}.isdisjoint(group_tool_names)


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
    assert json.loads(result)["path"] == "memory.md"


@pytest.mark.asyncio
async def test_group_workspace_tool_runs_commit_callbacks(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    db = _DB()

    async def committed() -> None:
        db.events.append("after_commit")

    async def rolled_back() -> None:
        db.events.append("after_rollback")

    async def write_workspace(db_arg, **kwargs):
        assert db_arg is db
        add_after_commit_callback(db_arg, committed)
        add_after_rollback_callback(db_arg, rolled_back)
        return group_file_service.GroupTextFile(
            path=kwargs["path"],
            content=kwargs["content"],
            exists=True,
            version_token="v2",
            modified_at="now",
        )

    monkeypatch.setattr(
        group_file_service,
        "write_workspace_file",
        write_workspace,
    )
    await GroupRuntimeToolService(session_factory=_factory(db)).execute(
        _state(
            tenant_id,
            group_id,
            session_id,
            agent,
            participant_id,
            group_context=True,
        ),
        agent,
        GROUP_WRITE_WORKSPACE_FILE,
        {"path": "report.md", "content": "done"},
    )

    assert db.events == ["commit", "after_commit"]
    assert db.info == {}


@pytest.mark.asyncio
async def test_group_workspace_tool_runs_rollback_callbacks(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    db = _DB()

    async def committed() -> None:
        db.events.append("after_commit")

    async def rolled_back() -> None:
        db.events.append("after_rollback")

    async def fail_workspace(db_arg, **_kwargs):
        assert db_arg is db
        add_after_commit_callback(db_arg, committed)
        add_after_rollback_callback(db_arg, rolled_back)
        raise RuntimeError("workspace write failed")

    monkeypatch.setattr(
        group_file_service,
        "write_workspace_file",
        fail_workspace,
    )
    with pytest.raises(RuntimeError, match="workspace write failed"):
        await GroupRuntimeToolService(session_factory=_factory(db)).execute(
            _state(
                tenant_id,
                group_id,
                session_id,
                agent,
                participant_id,
                group_context=True,
            ),
            agent,
            GROUP_WRITE_WORKSPACE_FILE,
            {"path": "report.md", "content": "done"},
        )

    assert db.events == ["rollback", "after_rollback"]
    assert db.info == {}
