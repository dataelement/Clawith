"""Cross-cutting Group A2A visibility and Runtime Thread identity regressions."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.models.session_context_state import SessionContextState
from app.models.workspace import WorkspaceFileRevision
from app.services.agent_runtime.a2a_completion import A2ARuntimeCompletionHandler
from app.services.agent_runtime.a2a_runtime import RuntimeA2AService
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.group_handoff import (
    GroupAgentHandoffIntent,
    _handoff_child_command,
)
from app.services.agent_runtime.planning import validate_planning_output
from app.services.agent_runtime.planning_scheduler import _entry_command
from app.services.agent_runtime.state import RunInputSnapshots, RuntimeGraphState
from app.services.agent_runtime.tool_execution import ToolExecutionReservation
from app.services.group_message_service import (
    ResolvedGroupMention,
    _planning_command,
    _SenderScope,
)


NOW = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    def __init__(self, db: "_Session") -> None:
        self.db = db

    async def __aenter__(self):
        self.db.transaction_depth += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self.db.transaction_depth -= 1
        return False


class _Session:
    def __init__(
        self,
        *results: object | None,
        records: dict[tuple[type, object], object] | None = None,
    ) -> None:
        self.results = deque(results)
        self.records = records or {}
        self.added: list[object] = []
        self.flushes = 0
        self.transaction_depth = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    def begin_nested(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, statement) -> _Result:
        del statement
        if not self.results:
            raise AssertionError("unexpected database query")
        return _Result(self.results.popleft())

    async def get(self, model, identity):
        return self.records.get((model, identity))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self) -> _Session:
        return self.sessions.popleft()


class _CycleGuard:
    async def ensure_delegation_allowed(self, db, **kwargs):
        del db, kwargs
        return SimpleNamespace(cycle_count=0)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=True,
        AGENT_RUNTIME_V2_SOURCE_TYPES="chat,a2a",
        AGENT_RUNTIME_GRAPH_NAME="runtime",
        AGENT_RUNTIME_GRAPH_VERSION="v1",
    )


def _agent(
    *,
    tenant_id: uuid.UUID,
    name: str,
    creator_id: uuid.UUID,
) -> tuple[Agent, LLMModel]:
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="group-contract-model",
        api_key_encrypted="encrypted",
        label=f"{name} model",
        enabled=True,
    )
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=creator_id,
        name=name,
        primary_model_id=model.id,
        status="idle",
        is_expired=False,
        agent_type="native",
        access_mode="company",
        max_tool_rounds=50,
    )
    return agent, model


def _mention(agent: Agent, model: LLMModel) -> ResolvedGroupMention:
    return ResolvedGroupMention(
        participant_id=uuid.uuid4(),
        participant_type="agent",
        participant_ref_id=agent.id,
        display_name=agent.name,
        valid=True,
        triggers_agent=True,
        agent=agent,
        model=model,
    )


def _group_scope(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[_SenderScope, ChatMessage]:
    participant = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user_id,
        display_name="Requestor",
    )
    group = Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Runtime contract group",
        created_by_participant_id=participant.id,
    )
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group.id,
        title="Runtime contract group",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_by_participant_id=participant.id,
    )
    scope = _SenderScope(
        group=group,
        session=session,
        participant=participant,
        user_id=user_id,
        agent_id=None,
        role="user",
    )
    message = ChatMessage(
        id=uuid.uuid4(),
        user_id=user_id,
        agent_id=None,
        role="user",
        content="Research and review the launch",
        conversation_id=str(session.id),
        participant_id=participant.id,
        mentions=[],
        created_at=NOW,
    )
    return scope, message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["consult", "task_delegate"])
async def test_group_source_a2a_stays_pair_private_and_resumes_exact_source_run(
    mode: str,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source_agent, source_model = _agent(
        tenant_id=tenant_id,
        name="Coordinator",
        creator_id=user_id,
    )
    target_agent, target_model = _agent(
        tenant_id=tenant_id,
        name="Researcher",
        creator_id=user_id,
    )
    scope, group_message = _group_scope(tenant_id=tenant_id, user_id=user_id)
    source_run_id = uuid.uuid4()
    source_run = AgentRun(
        id=source_run_id,
        tenant_id=tenant_id,
        agent_id=source_agent.id,
        session_id=scope.session.id,
        source_type="chat",
        source_id=str(group_message.id),
        source_execution_id=f"group_mention:{group_message.id}:agent:{source_agent.id}",
        origin_user_id=user_id,
        goal=group_message.content,
        run_kind="foreground",
        model_id=source_model.id,
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id=str(source_run_id),
        graph_name="runtime",
        graph_version="v1",
        lane_held=True,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(scope.session.id),
            "group_id": str(scope.group.id),
        },
    )
    ordered_agents = sorted((source_agent.id, target_agent.id), key=str)
    pair_session = ChatSession(
        id=uuid.uuid5(
            tenant_id,
            f"a2a-session:{ordered_agents[0]}:{ordered_agents[1]}",
        ),
        tenant_id=tenant_id,
        session_type="a2a",
        agent_id=ordered_agents[0],
        peer_agent_id=ordered_agents[1],
        user_id=user_id,
        title="Coordinator ↔ Researcher",
        source_channel="agent",
        is_group=False,
        is_primary=False,
    )
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=source_run.id,
        tool_call_id=f"{mode}-call",
        tool_name="send_message_to_agent",
        assistant_message_id="assistant-message",
        arguments_hash="arguments-hash",
        sanitized_arguments={},
        status="started",
        lease_owner=f"runtime:command:{mode}-call",
    )
    reservation = ToolExecutionReservation(
        execution=execution,
        created=True,
        retrying=False,
        reusable_result=None,
        prior_failure=None,
        blocked=False,
        reconciliation_required=False,
        requires_confirmation=False,
        error_code=None,
    )
    source_participant = SimpleNamespace(id=uuid.uuid4())
    target_participant = SimpleNamespace(id=uuid.uuid4())
    target_run_id = uuid.uuid4()
    target_handle = RunHandle(
        tenant_id=tenant_id,
        run_id=target_run_id,
        thread_id=str(target_run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )
    intake_db = _Session(source_run, source_agent, pair_session)

    async def mark_succeeded(db, **kwargs):
        assert db is intake_db
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        return execution

    forbidden_context_write = AsyncMock()
    forbidden_memory_write = AsyncMock()
    forbidden_workspace_write = AsyncMock()
    with (
        patch(
            "app.services.agent_runtime.a2a_runtime._resolve_target",
            new=AsyncMock(return_value=target_agent),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.get_or_create_agent_participant",
            new=AsyncMock(side_effect=(source_participant, target_participant)),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=target_handle),
        ) as start_run,
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_succeeded",
            new=AsyncMock(side_effect=mark_succeeded),
        ),
        patch(
            "app.services.agent_runtime.session_context_service.SessionContextService.compare_and_swap",
            new=forbidden_context_write,
        ),
        patch(
            "app.services.group_file_service.write_agent_memory",
            new=forbidden_memory_write,
        ),
        patch(
            "app.services.group_file_service.write_workspace_file",
            new=forbidden_workspace_write,
        ),
    ):
        accepted = await RuntimeA2AService(
            session_factory=_SessionFactory(intake_db),  # type: ignore[arg-type]
            settings=_settings(),
            cycle_guard=_CycleGuard(),  # type: ignore[arg-type]
        ).execute(
            tenant_id=tenant_id,
            source_run_id=source_run.id,
            source_agent_id=source_agent.id,
            tool_call_id=f"{mode}-call",
            arguments={
                "target_agent_id": str(target_agent.id),
                "message": "Check the private evidence",
                "msg_type": mode,
            },
            reservation=reservation,
            lease_owner=f"runtime:command:{mode}-call",
            actor_user_id=user_id,
        )

        target_command = start_run.await_args.args[0]
        assert isinstance(target_command, StartRunCommand)
        assert accepted.waiting_request is not None
        assert target_command.session_id == pair_session.id
        assert target_command.parent_run_id == source_run.id
        assert target_command.correlation_id == accepted.waiting_request["correlation_id"]

        target_run = AgentRun(
            id=target_run_id,
            tenant_id=tenant_id,
            agent_id=target_agent.id,
            session_id=pair_session.id,
            source_type="a2a",
            source_id=str(pair_session.id),
            source_execution_id=target_command.source_execution_id,
            correlation_id=target_command.correlation_id,
            origin_user_id=user_id,
            origin_agent_id=source_agent.id,
            parent_run_id=source_run.id,
            root_run_id=source_run.id,
            goal=target_command.goal,
            run_kind="delegated",
            model_id=target_model.id,
            model_turn_limit=50,
            runtime_type="langgraph",
            runtime_thread_id=str(target_run_id),
            graph_name="runtime",
            graph_version="v1",
            lane_held=False,
            delivery_status="not_required",
        )
        registry_run = RuntimeRunRecord(
            tenant_id=tenant_id,
            run_id=target_run.id,
            thread_id=target_run.runtime_thread_id,
            runtime_type="langgraph",
            goal=target_run.goal,
            run_kind=target_run.run_kind,
            source_type=target_run.source_type,
            model_id=str(target_model.id),
            graph_name="runtime",
            graph_version="v1",
            agent_id=str(target_agent.id),
            session_id=str(pair_session.id),
            parent_run_id=str(source_run.id),
            root_run_id=str(source_run.id),
        )
        state: RuntimeGraphState = {
            "snapshots": RunInputSnapshots(
                session_context={},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": "completed",
                "next_route": "terminal",
                "final_answer": "Private verified result",
                "result_summary": {
                    "summary": "Private verified result",
                    "artifact_refs": [],
                },
            },
        }
        checkpoint = CheckpointObservation(
            checkpoint_id=f"{mode}-terminal",
            state=state,
        )
        completion_db = _Session(
            target_run,
            None,
            source_run,
            target_agent,
            pair_session,
            source_run,
            source_run,
            None,
        )
        with patch(
            "app.services.agent_runtime.a2a_completion.get_or_create_agent_participant",
            new=AsyncMock(return_value=target_participant),
        ):
            await A2ARuntimeCompletionHandler(
                session_factory=_SessionFactory(completion_db),  # type: ignore[arg-type]
                clock=lambda: NOW,
            ).handle(run=registry_run, checkpoint=checkpoint)

    private_messages = [value for value in (*intake_db.added, *completion_db.added) if isinstance(value, ChatMessage)]
    assert [message.role for message in private_messages] == ["user", "assistant"]
    assert {pair_session.agent_id, pair_session.peer_agent_id} == {
        source_agent.id,
        target_agent.id,
    }
    assert all(message.conversation_id == str(pair_session.id) for message in private_messages)
    assert all(message.conversation_id != str(scope.session.id) for message in private_messages)
    assert not any(isinstance(value, ChatSession) for value in intake_db.added)
    assert not any(
        isinstance(value, (SessionContextState, WorkspaceFileRevision))
        for value in (*intake_db.added, *completion_db.added)
    )
    resume_commands = [value for value in completion_db.added if isinstance(value, AgentRunCommand)]
    assert len(resume_commands) == 1
    resume = resume_commands[0]
    assert resume.command_type == "resume"
    assert resume.run_id == source_run.id
    assert resume.payload["correlation_id"] == target_run.correlation_id
    assert source_run.runtime_thread_id == str(source_run.id)
    forbidden_context_write.assert_not_awaited()
    forbidden_memory_write.assert_not_awaited()
    forbidden_workspace_write.assert_not_awaited()


async def _persist_start(command: StartRunCommand, agent: Agent | None) -> AgentRun:
    results: list[object | None] = [None]
    if command.run_kind != "orchestration":
        assert agent is not None
        results.append(agent)
        results.append(
            LLMModel(
                id=command.model_id,
                tenant_id=command.tenant_id,
                provider="openai",
                model="group-contract-model",
                api_key_encrypted="encrypted",
                label="Group contract model",
                enabled=True,
                supports_tool_calling=True,
            )
        )
    results.append(None)
    db = _Session(*results)

    handle = await RuntimeCommandIntake(
        db,  # type: ignore[arg-type]
        settings=_settings(),
    ).start_run(command)

    run = next(value for value in db.added if isinstance(value, AgentRun))
    assert handle.run_id == run.id
    assert handle.thread_id == run.runtime_thread_id
    return run


@pytest.mark.asyncio
async def test_group_planning_entries_and_handoff_use_distinct_run_threads_while_direct_shares_session_thread() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    scope, message = _group_scope(tenant_id=tenant_id, user_id=user_id)
    first_agent, first_model = _agent(
        tenant_id=tenant_id,
        name="Researcher",
        creator_id=user_id,
    )
    second_agent, second_model = _agent(
        tenant_id=tenant_id,
        name="Reviewer",
        creator_id=user_id,
    )
    handoff_agent, handoff_model = _agent(
        tenant_id=tenant_id,
        name="Approver",
        creator_id=user_id,
    )
    first = _mention(first_agent, first_model)
    second = _mention(second_agent, second_model)
    handoff = _mention(handoff_agent, handoff_model)
    mentions = (first, second)
    message.mentions = [mention.payload() for mention in mentions]

    planning_model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="planning-model",
        api_key_encrypted="encrypted",
        label="Planning model",
        enabled=True,
    )
    root_command = _planning_command(
        tenant_id=tenant_id,
        scope=scope,
        message=message,
        mentions=mentions,
        targets=mentions,
        model=planning_model,
    )
    root = await _persist_start(root_command, None)
    plan = validate_planning_output(
        {
            "version": 2,
            "mode": "enforced",
            "goal": message.content,
            "plan_prompt": "Research first, then review, then hand off publicly.",
            "entry_steps": [
                {
                    "agent_id": str(first_agent.id),
                    "instruction": "Research the launch",
                },
                {
                    "agent_id": str(second_agent.id),
                    "instruction": "Review the launch",
                },
            ],
        },
        candidate_agent_ids=frozenset({first_agent.id, second_agent.id}),
    )
    entry_commands = tuple(
        _entry_command(
            root=root,
            message=message,
            scope=scope,
            mention_targets=message.mentions,
            plan=plan,
            entry=entry,
            target=target,
        )
        for entry, target in zip(plan["entry_steps"], mentions, strict=True)
    )
    first_entry = await _persist_start(entry_commands[0], first_agent)
    second_entry = await _persist_start(entry_commands[1], second_agent)

    agent_participant = Participant(
        id=first.participant_id,
        type="agent",
        ref_id=first_agent.id,
        display_name=first_agent.name,
    )
    agent_scope = _SenderScope(
        group=scope.group,
        session=scope.session,
        participant=agent_participant,
        user_id=None,
        agent_id=first_agent.id,
        role="assistant",
    )
    handoff_message_id = uuid.uuid4()
    intent = GroupAgentHandoffIntent(
        source_run_id=first_entry.id,
        source_agent_id=first_agent.id,
        sender_participant_id=agent_participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=first_entry.id,
        child_root_run_id=root.id,
        mention_participant_ids=(handoff.participant_id,),
        trigger_message_id=handoff_message_id,
        cutoff_created_at=NOW,
        idempotency_key=f"run:{first_entry.id}:terminal:completed",
        origin_user_id=user_id,
        mode=plan["mode"],
        plan_prompt=plan["plan_prompt"],
    )
    handoff_command = _handoff_child_command(
        source_run=first_entry,
        scope=agent_scope,
        intent=intent,
        content="The evidence is ready for final approval.",
        mentions=(handoff,),
        target=handoff,
    )
    handoff_run = await _persist_start(handoff_command, handoff_agent)

    group_commands = (root_command, *entry_commands, handoff_command)
    group_runs = (root, first_entry, second_entry, handoff_run)
    assert all(command.runtime_thread_id is None for command in group_commands)
    assert all(run.session_id == scope.session.id for run in group_runs)
    assert all(run.runtime_thread_id == str(run.id) for run in group_runs)
    assert len({run.runtime_thread_id for run in group_runs}) == len(group_runs)

    direct_session_id = uuid.uuid4()
    direct_commands = tuple(
        StartRunCommand(
            tenant_id=tenant_id,
            agent_id=first_agent.id,
            session_id=direct_session_id,
            source_type="chat",
            source_id=str(message_id),
            source_execution_id=f"chat:{message_id}",
            goal=f"Direct turn {index}",
            run_kind="foreground",
            model_id=first_model.id,
            runtime_thread_id=str(direct_session_id),
            delivery_status="pending",
            delivery_target={
                "kind": "direct",
                "session_id": str(direct_session_id),
                "user_id": str(user_id),
            },
            idempotency_key=f"start:chat:{message_id}",
            payload={"message_id": str(message_id)},
            origin_user_id=user_id,
            actor_user_id=user_id,
        )
        for index, message_id in enumerate((uuid.uuid4(), uuid.uuid4()), start=1)
    )
    direct_runs = (
        await _persist_start(direct_commands[0], first_agent),
        await _persist_start(direct_commands[1], first_agent),
    )
    assert direct_runs[0].id != direct_runs[1].id
    assert {
        direct_runs[0].runtime_thread_id,
        direct_runs[1].runtime_thread_id,
    } == {str(direct_session_id)}
