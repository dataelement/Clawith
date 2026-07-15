"""Committed Planning checkpoint scheduling and child resumption tests."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.planning import validate_planning_output
from app.services.agent_runtime.planning_scheduler import (
    PlanningCheckpointScheduler,
    PlanningChildCompletionHandler,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


NOW = datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
ORIGINAL_GROUP_REQUEST = (
    "Research the facts, preserve every source hash, then write the final answer.\n"
    "Do not replace this request with a shortened Planning instruction."
)


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, *results: object | None) -> None:
        self.results = deque(results)
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction()

    async def execute(self, statement):
        del statement
        if not self.results:
            raise AssertionError("unexpected database query")
        return _Result(self.results.popleft())

    async def flush(self):
        self.flushes += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self):
        return self.sessions.popleft()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=True,
        AGENT_RUNTIME_GRAPH_NAME="runtime",
        AGENT_RUNTIME_GRAPH_VERSION="v1",
    )


def _records():
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    group_id = uuid.uuid4()
    message_id = uuid.uuid4()
    root_id = uuid.uuid4()
    sender_participant_id = uuid.uuid4()
    first_agent_id, second_agent_id = uuid.uuid4(), uuid.uuid4()
    planning_model_id = uuid.uuid4()
    root = AgentRun(
        id=root_id,
        tenant_id=tenant_id,
        agent_id=None,
        session_id=session_id,
        source_type="chat",
        source_id=str(message_id),
        source_execution_id=f"group_mention:{message_id}:plan",
        origin_user_id=uuid.uuid4(),
        goal=ORIGINAL_GROUP_REQUEST,
        run_kind="orchestration",
        system_role="group_planning",
        model_id=planning_model_id,
        runtime_type="langgraph",
        runtime_thread_id=str(root_id),
        graph_name="runtime_group_planning",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(session_id),
            "group_id": str(group_id),
        },
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(root_id),
        goal=root.goal,
        run_kind="orchestration",
        source_type="chat",
        model_id=str(planning_model_id),
        graph_name="runtime_group_planning",
        graph_version="v1",
        session_id=str(session_id),
        system_role="group_planning",
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=root_id,
        thread_id=str(root_id),
        runtime_type="langgraph",
        registry=registry,
    )
    plan = validate_planning_output(
        {
            "version": 1,
            "goal": root.goal,
            "execution_strategy": "sequential",
            "steps": [
                {
                    "step_id": "research",
                    "agent_id": str(first_agent_id),
                    "instruction": "Research the facts",
                    "depends_on_step_ids": [],
                    "required_tool_names": [
                        "group_list_workspace",
                        "group_write_workspace_file",
                    ],
                    "required_artifact_paths": ["evidence/research.md"],
                },
                {
                    "step_id": "write",
                    "agent_id": str(second_agent_id),
                    "instruction": "Write the answer",
                    "depends_on_step_ids": ["research"],
                },
            ],
        },
        candidate_agent_ids=frozenset({first_agent_id, second_agent_id}),
    )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "sender_participant_id": str(sender_participant_id),
                "mention_targets": [
                    {"participant_id": str(sender_participant_id), "valid": True}
                ],
                "candidate_agents": [
                    {"agent_id": str(first_agent_id)},
                    {"agent_id": str(second_agent_id)},
                ],
            },
        ),
        "lifecycle": {
            "status": "waiting_agent",
            "next_route": "wait",
            "planning": plan,
            "waiting_request": {
                "waiting_type": "agent",
                "correlation_id": f"planning:{root_id}",
            },
        },
    }
    checkpoint = CheckpointObservation(checkpoint_id="planning-wait-1", state=state)
    message = ChatMessage(
        id=message_id,
        role="user",
        content=root.goal,
        conversation_id=str(session_id),
        participant_id=sender_participant_id,
        mentions=[],
        created_at=NOW,
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        title="Group",
        source_channel="web",
        is_group=True,
        is_primary=True,
    )
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Group",
        created_by_participant_id=sender_participant_id,
    )
    child_model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="child-model",
        api_key_encrypted="encrypted",
        label="Child",
        enabled=True,
    )
    agent = Agent(
        id=first_agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Researcher",
        primary_model_id=child_model.id,
        status="idle",
        is_expired=False,
        access_mode="company",
    )
    participant = Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=agent.id,
        display_name=agent.name,
    )
    membership = GroupMember(
        id=uuid.uuid4(),
        group_id=group_id,
        participant_id=participant.id,
        role="member",
        joined_at=NOW,
        session_read_state={},
    )
    return (
        run,
        checkpoint,
        root,
        message,
        session,
        group,
        agent,
        participant,
        membership,
        child_model,
    )


@pytest.mark.asyncio
async def test_waiting_checkpoint_creates_only_ready_child_run() -> None:
    (
        run,
        checkpoint,
        root,
        message,
        session,
        group,
        agent,
        participant,
        membership,
        child_model,
    ) = _records()
    db = _Session(
        root,
        message,
        session,
        group,
        None,
        agent,
        participant,
        membership,
        child_model,
    )
    handle = RunHandle(
        tenant_id=run.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with patch(
        "app.services.agent_runtime.planning_scheduler.TransactionalAgentRuntimeAdapter.start_run",
        new=AsyncMock(return_value=handle),
    ) as start_run:
        await PlanningCheckpointScheduler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
            settings=_settings(),
        ).handle(run=run, checkpoint=checkpoint)

    start_run.assert_awaited_once()
    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.source_execution_id == f"group_mention:{message.id}:step:research"
    assert command.parent_run_id == root.id
    assert command.root_run_id == root.id
    assert command.agent_id == agent.id
    assert command.goal == "Research the facts"
    assert command.scheduling_lane_key == f"group_mention:{run.tenant_id}:{agent.id}"
    assert command.scheduling_position_created_at == NOW
    assert command.scheduling_position_id == message.id
    assert command.payload["planning_step_id"] == "research"
    assert command.payload["planning_instruction"] == "Research the facts"
    assert command.payload["planning_required_tool_names"] == [
        "group_list_workspace",
        "group_write_workspace_file",
    ]
    assert command.payload["planning_required_artifact_paths"] == ["evidence/research.md"]
    assert command.payload["input_content"] == message.content
    assert command.payload["input_content"] != command.payload["planning_instruction"]
    assert "only the assigned Planning step" in command.payload["runtime_instruction"]
    assert "Research the facts" in command.payload["runtime_instruction"]
    assert command.payload["related_run_summaries"] == []


@pytest.mark.asyncio
async def test_existing_child_run_makes_checkpoint_scheduling_idempotent() -> None:
    (
        run,
        checkpoint,
        root,
        message,
        session,
        group,
        *_rest,
    ) = _records()
    db = _Session(root, message, session, group, uuid.uuid4())

    with patch(
        "app.services.agent_runtime.planning_scheduler.TransactionalAgentRuntimeAdapter.start_run",
        new=AsyncMock(),
    ) as start_run:
        await PlanningCheckpointScheduler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
            settings=_settings(),
        ).handle(run=run, checkpoint=checkpoint)

    start_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_planning_root_marks_delivery_not_required() -> None:
    run, checkpoint, root, *_rest = _records()
    checkpoint.state["lifecycle"].update(
        {"status": "completed", "next_route": "terminal"}
    )
    db = _Session(root)

    await PlanningCheckpointScheduler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        settings=_settings(),
    ).handle(run=run, checkpoint=checkpoint)

    assert root.delivery_status == "not_required"
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_child_terminal_checkpoint_resumes_planning_root_once() -> None:
    planning_run, _planning_checkpoint, root, message, session, *_rest = _records()
    child_id = uuid.uuid4()
    child_agent_id = uuid.uuid4()
    child = AgentRun(
        id=child_id,
        tenant_id=planning_run.tenant_id,
        agent_id=child_agent_id,
        session_id=session.id,
        source_type="chat",
        source_id=str(message.id),
        source_execution_id=f"group_mention:{message.id}:step:research",
        parent_run_id=root.id,
        root_run_id=root.id,
        goal="Research",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(child_id),
        graph_name="runtime",
        graph_version="v1",
        lane_held=False,
        delivery_status="delivered",
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(child.tenant_id),
        run_id=str(child.id),
        goal=child.goal,
        run_kind="foreground",
        source_type="chat",
        model_id=str(child.model_id),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(child.agent_id),
        session_id=str(child.session_id),
        parent_run_id=str(root.id),
        root_run_id=str(root.id),
    )
    child_run = RuntimeRunRecord(
        tenant_id=child.tenant_id,
        run_id=child.id,
        thread_id=str(child.id),
        runtime_type="langgraph",
        registry=registry,
    )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": "completed",
            "next_route": "terminal",
            "result_summary": {
                "summary": "Research complete",
                "artifact_refs": ["workspace:research.md"],
            },
        },
    }
    checkpoint = CheckpointObservation(checkpoint_id="child-terminal", state=state)
    db = _Session(child, root)

    with patch(
        "app.services.agent_runtime.planning_scheduler.enqueue_resume",
        new=AsyncMock(),
    ) as enqueue:
        await PlanningChildCompletionHandler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        ).handle(run=child_run, checkpoint=checkpoint)

    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["run_id"] == root.id
    assert enqueue.await_args.kwargs["idempotency_key"] == (
        f"resume:planning:{root.id}:child:{child.id}:terminal:completed"
    )
    assert enqueue.await_args.kwargs["payload"] == {
        "resume_type": "agent_result",
        "correlation_id": f"planning:{root.id}",
        "payload": {
            "step_id": "research",
            "status": "completed",
            "child_run_id": str(child.id),
            "result_summary": {
                "summary": "Research complete",
                "artifact_refs": ["workspace:research.md"],
            },
            "error": None,
        },
    }
