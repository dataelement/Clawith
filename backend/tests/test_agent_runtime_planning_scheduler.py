"""Planning v2 committed-checkpoint entry scheduling tests."""

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
from app.models.group import Group
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
    PlanningSchedulingError,
)
from app.services.agent_runtime.state import RunInputSnapshots, RuntimeGraphState
from app.services.group_message_service import ResolvedGroupMention, _SenderScope


NOW = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    def __init__(self, db: "_Session") -> None:
        self.db = db
        self.added_size = len(db.added)
        self.delivery_status = db.root.delivery_status
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc, traceback
        if exc_type is not None:
            del self.db.added[self.added_size :]
            self.db.root.delivery_status = self.delivery_status
            self.rolled_back = True
        return False


class _Session:
    def __init__(self, root: AgentRun, *results: object | None) -> None:
        self.root = root
        self.results = deque((root, *results))
        self.flushes = 0
        self.added: list[object] = []
        self.transaction: _Transaction | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        self.transaction = _Transaction(self)
        return self.transaction

    async def execute(self, statement):
        del statement
        if not self.results:
            raise AssertionError("unexpected database query")
        return _Result(self.results.popleft())

    async def flush(self):
        self.flushes += 1

    def add(self, value: object) -> None:
        self.added.append(value)


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self):
        return self.sessions.popleft()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=True,
        AGENT_RUNTIME_V2_SOURCE_TYPES="chat,a2a",
        AGENT_RUNTIME_GRAPH_NAME="runtime",
        AGENT_RUNTIME_GRAPH_VERSION="v1",
    )


def _target(
    *,
    tenant_id: uuid.UUID,
    name: str,
) -> ResolvedGroupMention:
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="child-model",
        api_key_encrypted="encrypted",
        label=f"{name} Model",
        enabled=True,
    )
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name=name,
        primary_model_id=model.id,
        status="idle",
        is_expired=False,
        access_mode="company",
        max_tool_rounds=50,
    )
    return ResolvedGroupMention(
        participant_id=uuid.uuid4(),
        participant_type="agent",
        participant_ref_id=agent.id,
        display_name=name,
        valid=True,
        triggers_agent=True,
        agent=agent,
        model=model,
    )


def _records():
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    root_id = uuid.uuid4()
    origin_user_id = uuid.uuid4()
    sender = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=origin_user_id,
        display_name="Requestor",
    )
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Planning Group",
        created_by_participant_id=sender.id,
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        title="Planning Group",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_by_participant_id=sender.id,
    )
    scope = _SenderScope(
        group=group,
        session=session,
        participant=sender,
        user_id=origin_user_id,
        agent_id=None,
        role="user",
    )
    first = _target(tenant_id=tenant_id, name="Researcher")
    second = _target(tenant_id=tenant_id, name="Reviewer")
    non_entry = _target(tenant_id=tenant_id, name="Observer")
    candidates = (first, second, non_entry)
    plan = validate_planning_output(
        {
            "version": 2,
            "mode": "enforced",
            "goal": "Research and review the launch",
            "plan_prompt": (
                "The Researcher gathers evidence. The Reviewer checks the evidence, "
                "and each further handoff must be public."
            ),
            "entry_steps": [
                {
                    "agent_id": str(first.agent.id),
                    "instruction": "Gather the launch evidence",
                },
                {
                    "agent_id": str(second.agent.id),
                    "instruction": "Review the launch evidence independently",
                },
            ],
        },
        candidate_agent_ids=frozenset(target.agent.id for target in candidates),
    )
    mentions = [target.payload() for target in candidates]
    message = ChatMessage(
        id=message_id,
        user_id=origin_user_id,
        agent_id=None,
        role="user",
        content="Research and review the launch",
        conversation_id=str(session_id),
        participant_id=sender.id,
        mentions=mentions,
        created_at=NOW,
    )
    root = AgentRun(
        id=root_id,
        tenant_id=tenant_id,
        agent_id=None,
        session_id=session_id,
        source_type="chat",
        source_id=str(message_id),
        source_execution_id=f"group_mention:{message_id}:plan",
        origin_user_id=origin_user_id,
        goal=message.content,
        run_kind="orchestration",
        system_role="group_planning",
        model_id=uuid.uuid4(),
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
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=root_id,
        thread_id=str(root_id),
        runtime_type="langgraph",
        goal=root.goal,
        run_kind=root.run_kind,
        source_type=root.source_type,
        model_id=str(root.model_id),
        graph_name=root.graph_name,
        graph_version=root.graph_version,
        agent_id=None,
        session_id=str(session_id),
        system_role="group_planning",
    )
    state: RuntimeGraphState = {
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "message_id": str(message.id),
                "group_id": str(group.id),
                "session_id": str(session.id),
                "sender_participant_id": str(sender.id),
                "mention_targets": mentions,
                "candidate_agents": [
                    {
                        "agent_id": str(target.agent.id),
                        "participant_id": str(target.participant_id),
                        "name": target.display_name,
                    }
                    for target in candidates
                ],
            },
        ),
        "messages": [],
        "lifecycle": {
            "status": "completed",
            "next_route": "terminal",
            "planning": plan,
            "waiting_request": None,
        },
    }
    checkpoint = CheckpointObservation(
        checkpoint_id="planning-v2-terminal",
        state=state,
    )
    return run, checkpoint, root, message, scope, candidates, plan


def _handle(tenant_id: uuid.UUID, *, created: bool = True) -> RunHandle:
    run_id = uuid.uuid4()
    return RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=created,
    )


@pytest.mark.asyncio
async def test_completed_plan_creates_only_entry_children_with_one_immutable_plan() -> None:
    run, checkpoint, root, message, scope, candidates, plan = _records()
    first, second, non_entry = candidates
    db = _Session(root, message)
    start = AsyncMock(side_effect=(_handle(run.tenant_id), _handle(run.tenant_id)))

    with (
        patch(
            "app.services.agent_runtime.planning_scheduler._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler.RuntimeCommandIntake.start_run",
            new=start,
        ),
    ):
        await PlanningCheckpointScheduler(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
            settings=_settings(),
        ).handle(run=run, checkpoint=checkpoint)

    assert root.delivery_status == "not_required"
    assert db.flushes == 1
    assert start.await_count == 2
    commands = [call.args[0] for call in start.await_args_list]
    assert all(isinstance(command, StartRunCommand) for command in commands)
    assert [command.agent_id for command in commands] == [
        first.agent.id,
        second.agent.id,
    ]
    assert non_entry.agent.id not in {command.agent_id for command in commands}
    assert [command.goal for command in commands] == [
        "Gather the launch evidence",
        "Review the launch evidence independently",
    ]
    assert all(command.parent_run_id == root.id for command in commands)
    assert all(command.root_run_id == root.id for command in commands)
    assert all(command.source_id == str(message.id) for command in commands)
    assert all(command.scheduling_position_created_at == NOW for command in commands)
    assert all(command.scheduling_position_id == message.id for command in commands)
    assert all(command.payload["mode"] == plan["mode"] for command in commands)
    assert all(command.payload["plan_prompt"] == plan["plan_prompt"] for command in commands)
    assert all(
        command.payload["context_cutoff"] == {"message_id": str(message.id), "created_at": NOW.isoformat()}
        for command in commands
    )
    assert [command.payload["current_responsibility"] for command in commands] == [
        "Gather the launch evidence",
        "Review the launch evidence independently",
    ]
    assert all("planning_step_id" not in command.payload for command in commands)
    assert all("planning_instruction" not in command.payload for command in commands)
    assert all("related_run_summaries" not in command.payload for command in commands)


@pytest.mark.asyncio
async def test_completed_plan_product_retry_is_idempotent() -> None:
    run, checkpoint, root, message, scope, candidates, _ = _records()
    first, second, _ = candidates
    first_db = _Session(root, message)
    second_db = _Session(root, message)
    created_source_ids: set[str] = set()
    created_runs = 0

    async def start_run(command: StartRunCommand) -> RunHandle:
        nonlocal created_runs
        created = command.source_execution_id not in created_source_ids
        if created:
            created_source_ids.add(command.source_execution_id)
            created_runs += 1
        return _handle(run.tenant_id, created=created)

    with (
        patch(
            "app.services.agent_runtime.planning_scheduler._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler.RuntimeCommandIntake.start_run",
            new=AsyncMock(side_effect=start_run),
        ) as start,
    ):
        scheduler = PlanningCheckpointScheduler(
            session_factory=_SessionFactory(first_db, second_db),  # type: ignore[arg-type]
            settings=_settings(),
        )
        await scheduler.handle(run=run, checkpoint=checkpoint)
        await scheduler.handle(run=run, checkpoint=checkpoint)

    assert start.await_count == 4
    assert created_runs == 2
    assert created_source_ids == {
        f"group_mention:{message.id}:entry:{first.agent.id}",
        f"group_mention:{message.id}:entry:{second.agent.id}",
    }
    first_attempt = [call.args[0] for call in start.await_args_list[:2]]
    second_attempt = [call.args[0] for call in start.await_args_list[2:]]
    assert [command.idempotency_key for command in first_attempt] == [
        command.idempotency_key for command in second_attempt
    ]


@pytest.mark.asyncio
async def test_entry_revalidation_failure_creates_no_partial_child() -> None:
    run, checkpoint, root, message, scope, candidates, _ = _records()
    first, second, _ = candidates
    invalid = ResolvedGroupMention(
        participant_id=second.participant_id,
        participant_type="agent",
        participant_ref_id=second.agent.id,
        display_name=second.display_name,
        valid=False,
        triggers_agent=False,
        reason="agent_unavailable",
    )
    db = _Session(root, message)
    start = AsyncMock()

    with (
        patch(
            "app.services.agent_runtime.planning_scheduler._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler._resolve_mentions",
            new=AsyncMock(return_value=(first, invalid)),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler.RuntimeCommandIntake.start_run",
            new=start,
        ),
    ):
        with pytest.raises(PlanningSchedulingError) as raised:
            await PlanningCheckpointScheduler(
                session_factory=_SessionFactory(db),  # type: ignore[arg-type]
                settings=_settings(),
            ).handle(run=run, checkpoint=checkpoint)

    assert raised.value.code == "planning_entry_unavailable"
    start.assert_not_awaited()
    assert root.delivery_status == "pending"
    assert db.transaction is not None and db.transaction.rolled_back is True


@pytest.mark.asyncio
async def test_later_child_write_failure_rolls_back_the_whole_entry_batch() -> None:
    run, checkpoint, root, message, scope, candidates, _ = _records()
    first, second, _ = candidates
    db = _Session(root, message)
    calls = 0

    async def start_run(command: StartRunCommand) -> RunHandle:
        nonlocal calls
        calls += 1
        db.add(("child", command.agent_id))
        if calls == 2:
            raise RuntimeError("second child write failed")
        return _handle(run.tenant_id)

    with (
        patch(
            "app.services.agent_runtime.planning_scheduler._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.planning_scheduler.RuntimeCommandIntake.start_run",
            new=AsyncMock(side_effect=start_run),
        ),
    ):
        with pytest.raises(RuntimeError, match="second child"):
            await PlanningCheckpointScheduler(
                session_factory=_SessionFactory(db),  # type: ignore[arg-type]
                settings=_settings(),
            ).handle(run=run, checkpoint=checkpoint)

    assert db.added == []
    assert root.delivery_status == "pending"
    assert db.transaction is not None and db.transaction.rolled_back is True


@pytest.mark.asyncio
async def test_noncompleted_planning_checkpoint_never_schedules_or_resumes() -> None:
    run, checkpoint, root, _message, _scope, _candidates, _plan = _records()
    checkpoint.state["lifecycle"].update(
        {
            "status": "waiting_agent",
            "next_route": "wait",
            "waiting_request": {
                "waiting_type": "agent",
                "correlation_id": f"planning:{root.id}",
            },
        }
    )
    factory = _SessionFactory()

    await PlanningCheckpointScheduler(
        session_factory=factory,  # type: ignore[arg-type]
        settings=_settings(),
    ).handle(run=run, checkpoint=checkpoint)

    assert not factory.sessions
