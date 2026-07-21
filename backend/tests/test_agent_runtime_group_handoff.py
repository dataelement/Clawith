"""Group Agent terminal public-mention handoff contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.cycle_guard import AgentCycleCheck, AgentCycleGuardError
from app.services.agent_runtime.group_handoff import (
    GroupAgentHandoffError,
    GroupAgentHandoffIntent,
    apply_group_agent_handoff,
    preflight_group_agent_handoff,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.group_message_service import (
    GroupMessageServiceError,
    ResolvedGroupMention,
    _SenderScope,
)


NOW = datetime(2026, 7, 16, 13, 30, tzinfo=UTC)


class _NoopExecutor:
    async def execute(self, *args, **kwargs):  # pragma: no cover - protocol stub
        raise AssertionError("not used")


class _DB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:  # pragma: no cover - defensive contract
        raise AssertionError("handoff must use the caller transaction")

    async def rollback(self) -> None:  # pragma: no cover - defensive contract
        raise AssertionError("handoff must use the caller transaction")


class _RollbackTransaction:
    """Model the caller-owned transaction boundary used by product sync."""

    def __init__(self, db: _DB) -> None:
        self.db = db
        self.snapshot_size = len(db.added)
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc, traceback
        if exc_type is not None:
            del self.db.added[self.snapshot_size :]
            self.rolled_back = True
        return False


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=True,
        AGENT_RUNTIME_V2_SOURCE_TYPES="chat,a2a",
        MAX_AGENT_CYCLE_COUNT=5,
    )


def _records():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source_agent_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    source_run_id = uuid.uuid4()
    planning_root_id = uuid.uuid4()
    source_participant = Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=source_agent_id,
        display_name="Source Agent",
    )
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Delivery Group",
        created_by_participant_id=source_participant.id,
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=source_participant.id,
        title="Group session",
        source_channel="web",
        is_group=True,
        is_primary=True,
    )
    source_run = AgentRun(
        id=source_run_id,
        tenant_id=tenant_id,
        agent_id=source_agent_id,
        session_id=session_id,
        source_type="chat",
        source_id=str(uuid.uuid4()),
        source_execution_id=f"group-source:{source_run_id}",
        correlation_id=None,
        origin_user_id=user_id,
        origin_agent_id=uuid.uuid4(),
        parent_run_id=planning_root_id,
        root_run_id=planning_root_id,
        goal="Review the proposal",
        run_kind="delegated",
        system_role=None,
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id=str(source_run_id),
        graph_name="agent_runtime",
        graph_version="v2",
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(session_id),
            "group_id": str(group_id),
        },
    )
    scope = _SenderScope(
        group=group,
        session=session,
        participant=source_participant,
        user_id=None,
        agent_id=source_agent_id,
        role="assistant",
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(source_run_id),
        command_id=str(uuid.uuid4()),
        executor=_NoopExecutor(),
        goal=source_run.goal,
        run_kind=source_run.run_kind,
        source_type=source_run.source_type,
        model_id=str(source_run.model_id),
        graph_name=source_run.graph_name,
        graph_version=source_run.graph_version,
        agent_id=str(source_agent_id),
        session_id=str(session_id),
        parent_run_id=str(planning_root_id),
        root_run_id=str(planning_root_id),
        model_turn_limit=50,
        actor_user_id=str(user_id),
        actor_agent_id=str(source_agent_id),
    )
    state: RuntimeGraphState = {
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={
                "message_id": source_run.source_id,
                "group_id": str(group_id),
                "session_id": str(session_id),
                "sender_participant_id": str(uuid.uuid4()),
                "target_participant_id": str(source_participant.id),
                "mode": "enforced",
                "plan_prompt": "Reviewer hands the result to the final approver.",
                "group_context": {
                    "agent": {
                        "agent_id": str(source_agent_id),
                        "participant_id": str(source_participant.id),
                    },
                    "group": {"group_id": str(group_id)},
                    "session": {"session_id": str(session_id)},
                    "planning_hint": {
                        "mode": "enforced",
                        "plan_prompt": "Reviewer hands the result to the final approver.",
                    },
                },
            },
        ),
        "messages": [],
        "lifecycle": {"status": "running", "next_route": "model"},
    }
    return source_run, scope, context, state


def _target(
    *,
    tenant_id: uuid.UUID,
    participant_id: uuid.UUID | None = None,
    name: str = "Target Agent",
) -> ResolvedGroupMention:
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="gpt-test",
        api_key_encrypted="secret",
        label="Test",
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
        participant_id=participant_id or uuid.uuid4(),
        participant_type="agent",
        participant_ref_id=agent.id,
        display_name=agent.name,
        valid=True,
        triggers_agent=True,
        agent=agent,
        model=model,
    )


def test_frozen_intent_rejects_a_noncanonical_participant_sequence() -> None:
    source_run, scope, _, _ = _records()
    target = _target(tenant_id=source_run.tenant_id)
    intent = GroupAgentHandoffIntent(
        source_run_id=source_run.id,
        source_agent_id=source_run.agent_id,
        sender_participant_id=scope.participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=source_run.id,
        child_root_run_id=source_run.root_run_id or source_run.id,
        mention_participant_ids=(target.participant_id,),
        trigger_message_id=uuid.uuid5(source_run.id, "canonical-handoff-message"),
        cutoff_created_at=NOW,
        idempotency_key=f"run:{source_run.id}:terminal:completed",
        origin_user_id=source_run.origin_user_id,
        mode="enforced",
        plan_prompt="Review then approve.",
    )
    payload = intent.payload()
    payload["mention_participant_ids"] = [
        str(target.participant_id),
        str(target.participant_id),
    ]

    with pytest.raises(GroupAgentHandoffError) as raised:
        GroupAgentHandoffIntent.from_payload(payload)

    assert raised.value.code == "group_handoff_intent_invalid"


def _cycle_check() -> AgentCycleCheck:
    return AgentCycleCheck(cycle_count=0, ancestor_depth=2, edge_counts=())


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_status", ["pending", "delivered"])
async def test_preflight_freezes_all_targets_scope_lineage_plan_and_cutoff(
    delivery_status: str,
) -> None:
    source_run, scope, context, state = _records()
    source_run.delivery_status = delivery_status
    first = _target(tenant_id=source_run.tenant_id)
    second = _target(tenant_id=source_run.tenant_id, name="Final Approver")
    ensure = AsyncMock(return_value=_cycle_check())

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_source_run",
            new=AsyncMock(return_value=source_run),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=ensure,
        ),
    ):
        intent = await preflight_group_agent_handoff(
            _DB(),  # type: ignore[arg-type]
            state=state,
            context=context,
            content="Evidence is complete. Please perform final approval.",
            mention_participant_ids=(
                str(first.participant_id),
                str(second.participant_id),
            ),
            settings=_settings(),
            clock=lambda: NOW,
        )

    assert intent.source_run_id == source_run.id
    assert intent.source_agent_id == source_run.agent_id
    assert intent.sender_participant_id == scope.participant.id
    assert intent.group_id == scope.group.id
    assert intent.session_id == scope.session.id
    assert intent.child_parent_run_id == source_run.id
    assert intent.child_root_run_id == source_run.root_run_id
    assert intent.mention_participant_ids == (
        first.participant_id,
        second.participant_id,
    )
    assert intent.mode == "enforced"
    assert intent.plan_prompt == "Reviewer hands the result to the final approver."
    assert intent.cutoff_created_at == NOW
    assert intent.trigger_message_id == uuid.uuid5(
        source_run.id,
        f"delivery-message:{intent.idempotency_key}",
    )
    assert intent.idempotency_key == f"run:{source_run.id}:terminal:completed"
    assert ensure.await_count == 2

    restored = GroupAgentHandoffIntent.from_payload(intent.payload())
    assert restored == intent


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_status", ["failed", "not_required"])
async def test_preflight_rejects_non_delivery_group_sources(
    delivery_status: str,
) -> None:
    source_run, _, context, state = _records()
    source_run.delivery_status = delivery_status
    target_id = uuid.uuid4()

    with patch(
        "app.services.agent_runtime.group_handoff._load_source_run",
        new=AsyncMock(return_value=source_run),
    ):
        with pytest.raises(GroupAgentHandoffError) as raised:
            await preflight_group_agent_handoff(
                _DB(),  # type: ignore[arg-type]
                state=state,
                context=context,
                content="Please continue",
                mention_participant_ids=(str(target_id),),
                settings=_settings(),
                clock=lambda: NOW,
            )

    assert raised.value.code == "group_handoff_source_invalid"
    assert raised.value.repairable is False


@pytest.mark.asyncio
async def test_multi_target_preflight_failure_is_all_or_none_and_repairable() -> None:
    source_run, scope, context, state = _records()
    valid = _target(tenant_id=source_run.tenant_id)
    invalid_id = uuid.uuid4()
    invalid = ResolvedGroupMention(
        participant_id=invalid_id,
        participant_type=None,
        participant_ref_id=None,
        display_name=None,
        valid=False,
        triggers_agent=False,
        reason="not_group_member",
    )
    ensure = AsyncMock(return_value=_cycle_check())

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_source_run",
            new=AsyncMock(return_value=source_run),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(valid, invalid)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=ensure,
        ),
    ):
        with pytest.raises(GroupAgentHandoffError) as raised:
            await preflight_group_agent_handoff(
                _DB(),  # type: ignore[arg-type]
                state=state,
                context=context,
                content="Please continue",
                mention_participant_ids=(str(valid.participant_id), str(invalid_id)),
                settings=_settings(),
                clock=lambda: NOW,
            )

    assert raised.value.code == "group_handoff_target_invalid"
    assert raised.value.repairable is True
    assert ensure.await_count == 0


@pytest.mark.asyncio
async def test_cycle_limit_fails_preflight_before_terminal() -> None:
    source_run, scope, context, state = _records()
    target = _target(tenant_id=source_run.tenant_id)

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_source_run",
            new=AsyncMock(return_value=source_run),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(target,)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=AsyncMock(
                side_effect=AgentCycleGuardError(
                    "agent_cycle_limit_reached",
                    "cycle limit reached",
                )
            ),
        ),
    ):
        with pytest.raises(GroupAgentHandoffError) as raised:
            await preflight_group_agent_handoff(
                _DB(),  # type: ignore[arg-type]
                state=state,
                context=context,
                content="Continue",
                mention_participant_ids=(str(target.participant_id),),
                settings=_settings(),
                clock=lambda: NOW,
            )

    assert raised.value.code == "agent_cycle_limit_reached"
    assert raised.value.repairable is True


@pytest.mark.asyncio
async def test_atomic_apply_creates_public_message_and_one_new_child_per_target() -> None:
    source_run, scope, context, state = _records()
    # Group start ACK delivery precedes the terminal handoff in production.
    source_run.delivery_status = "delivered"
    first = _target(tenant_id=source_run.tenant_id)
    second = _target(tenant_id=source_run.tenant_id, name="Final Approver")
    ensure = AsyncMock(return_value=_cycle_check())
    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_source_run",
            new=AsyncMock(return_value=source_run),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=ensure,
        ),
    ):
        intent = await preflight_group_agent_handoff(
            _DB(),  # type: ignore[arg-type]
            state=state,
            context=context,
            content="Evidence is complete. Please perform final approval.",
            mention_participant_ids=(str(first.participant_id), str(second.participant_id)),
            settings=_settings(),
            clock=lambda: NOW,
        )

    message = ChatMessage(
        id=intent.trigger_message_id,
        agent_id=source_run.agent_id,
        user_id=None,
        role="assistant",
        content="Evidence is complete. Please perform final approval.",
        conversation_id=str(scope.session.id),
        participant_id=scope.participant.id,
        mentions=[first.payload(), second.payload()],
        created_at=NOW,
    )
    first_handle = RunHandle(
        tenant_id=source_run.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )
    second_handle = RunHandle(
        tenant_id=source_run.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )
    start = AsyncMock(side_effect=(first_handle, second_handle))
    db = _DB()

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=AsyncMock(return_value=_cycle_check()),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._persist_message",
            new=AsyncMock(return_value=(message, True)),
        ) as persist,
        patch(
            "app.services.agent_runtime.group_handoff.RuntimeCommandIntake.start_run",
            new=start,
        ),
    ):
        result = await apply_group_agent_handoff(
            db,  # type: ignore[arg-type]
            source_run=source_run,
            content=message.content,
            intent_payload=intent.payload(),
            expected_idempotency_key=intent.idempotency_key,
            expected_message_id=intent.trigger_message_id,
            settings=_settings(),
        )

    assert result.message is message
    assert result.run_handles == (first_handle, second_handle)
    persist.assert_awaited_once()
    assert start.await_count == 2
    commands = [call.args[0] for call in start.await_args_list]
    assert all(isinstance(command, StartRunCommand) for command in commands)
    assert [command.agent_id for command in commands] == [
        first.agent.id,
        second.agent.id,
    ]
    assert all(command.run_kind == "delegated" for command in commands)
    assert all(command.parent_run_id == source_run.id for command in commands)
    assert all(command.root_run_id == source_run.root_run_id for command in commands)
    assert all(command.source_id == str(message.id) for command in commands)
    assert all(command.goal == command.payload["current_responsibility"] for command in commands)
    assert first.display_name in commands[0].goal
    assert second.display_name in commands[1].goal
    assert all("Respond in the current group as yourself only" in command.goal for command in commands)
    assert all("Do not repeat or forward the source message" in command.goal for command in commands)
    assert all("Reply once and normally finish without mentioning anyone" in command.goal for command in commands)
    assert all("write its display name without @" in command.goal for command in commands)
    assert all(f"Source message:\n{message.content}" in command.goal for command in commands)
    assert all(command.payload["mode"] == "enforced" for command in commands)
    assert all(
        command.payload["plan_prompt"]
        == "Reviewer hands the result to the final approver."
        for command in commands
    )
    assert all(
        command.payload["context_cutoff"]
        == {"message_id": str(message.id), "created_at": NOW.isoformat()}
        for command in commands
    )
    assert all(command.origin_agent_id == source_run.agent_id for command in commands)
    assert all(command.actor_agent_id == source_run.agent_id for command in commands)
    assert all(command.idempotency_key.startswith("start:group_mention:") for command in commands)


@pytest.mark.asyncio
async def test_apply_revalidates_all_targets_before_any_product_write() -> None:
    source_run, scope, _, _ = _records()
    valid = _target(tenant_id=source_run.tenant_id)
    invalid_id = uuid.uuid4()
    intent = GroupAgentHandoffIntent(
        source_run_id=source_run.id,
        source_agent_id=source_run.agent_id,
        sender_participant_id=scope.participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=source_run.id,
        child_root_run_id=source_run.root_run_id or source_run.id,
        mention_participant_ids=(valid.participant_id, invalid_id),
        trigger_message_id=uuid.uuid5(source_run.id, "handoff-message"),
        cutoff_created_at=NOW,
        idempotency_key=f"run:{source_run.id}:terminal:completed",
        origin_user_id=source_run.origin_user_id,
        mode=None,
        plan_prompt=None,
    )
    invalid = ResolvedGroupMention(
        participant_id=invalid_id,
        participant_type=None,
        participant_ref_id=None,
        display_name=None,
        valid=False,
        triggers_agent=False,
        reason="agent_unavailable",
    )
    start = AsyncMock()
    persist = AsyncMock()

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(valid, invalid)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._persist_message",
            new=persist,
        ),
        patch(
            "app.services.agent_runtime.group_handoff.RuntimeCommandIntake.start_run",
            new=start,
        ),
    ):
        with pytest.raises(GroupAgentHandoffError) as raised:
            await apply_group_agent_handoff(
                _DB(),  # type: ignore[arg-type]
                source_run=source_run,
                content="Continue",
                intent_payload=intent.payload(),
                expected_idempotency_key=intent.idempotency_key,
                expected_message_id=intent.trigger_message_id,
                settings=_settings(),
            )

    assert raised.value.code == "group_handoff_target_invalid"
    start.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_revalidation_cannot_reorder_the_frozen_targets() -> None:
    source_run, scope, _, _ = _records()
    first = _target(tenant_id=source_run.tenant_id)
    second = _target(tenant_id=source_run.tenant_id, name="Final Approver")
    intent = GroupAgentHandoffIntent(
        source_run_id=source_run.id,
        source_agent_id=source_run.agent_id,
        sender_participant_id=scope.participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=source_run.id,
        child_root_run_id=source_run.root_run_id or source_run.id,
        mention_participant_ids=(first.participant_id, second.participant_id),
        trigger_message_id=uuid.uuid5(source_run.id, "ordered-handoff-message"),
        cutoff_created_at=NOW,
        idempotency_key=f"run:{source_run.id}:terminal:completed",
        origin_user_id=source_run.origin_user_id,
        mode="enforced",
        plan_prompt="Review then approve.",
    )
    start = AsyncMock()
    persist = AsyncMock()

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(second, first)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._persist_message",
            new=persist,
        ),
        patch(
            "app.services.agent_runtime.group_handoff.RuntimeCommandIntake.start_run",
            new=start,
        ),
    ):
        with pytest.raises(GroupAgentHandoffError) as raised:
            await apply_group_agent_handoff(
                _DB(),  # type: ignore[arg-type]
                source_run=source_run,
                content="Continue in the frozen order.",
                intent_payload=intent.payload(),
                expected_idempotency_key=intent.idempotency_key,
                expected_message_id=intent.trigger_message_id,
                settings=_settings(),
            )

    assert raised.value.code == "group_handoff_target_invalid"
    start.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_apply_does_not_move_the_session_clock_backwards() -> None:
    source_run, scope, _, _ = _records()
    target = _target(tenant_id=source_run.tenant_id)
    later = NOW + timedelta(minutes=5)
    scope.session.last_message_at = later
    scope.session.updated_at = later
    intent = GroupAgentHandoffIntent(
        source_run_id=source_run.id,
        source_agent_id=source_run.agent_id,
        sender_participant_id=scope.participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=source_run.id,
        child_root_run_id=source_run.root_run_id or source_run.id,
        mention_participant_ids=(target.participant_id,),
        trigger_message_id=uuid.uuid5(source_run.id, "delayed-handoff-message"),
        cutoff_created_at=NOW,
        idempotency_key=f"run:{source_run.id}:terminal:completed",
        origin_user_id=source_run.origin_user_id,
        mode="enforced",
        plan_prompt="Review then approve.",
    )
    message = ChatMessage(
        id=intent.trigger_message_id,
        agent_id=source_run.agent_id,
        user_id=None,
        role="assistant",
        content="Public review result",
        conversation_id=str(scope.session.id),
        participant_id=scope.participant.id,
        mentions=[target.payload()],
        created_at=NOW,
    )
    run_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=source_run.tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    async def persist_message(*args, **kwargs):
        del args
        scope.session.last_message_at = kwargs["clock"]
        scope.session.updated_at = kwargs["clock"]
        return message, True

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(target,)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=AsyncMock(return_value=_cycle_check()),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._persist_message",
            new=AsyncMock(side_effect=persist_message),
        ),
    ):
        await apply_group_agent_handoff(
            _DB(),  # type: ignore[arg-type]
            source_run=source_run,
            content=message.content,
            intent_payload=intent.payload(),
            expected_idempotency_key=intent.idempotency_key,
            expected_message_id=intent.trigger_message_id,
            settings=_settings(),
        )

    assert scope.session.last_message_at == later
    assert scope.session.updated_at == later


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["second_child", "message"])
async def test_caller_transaction_rolls_back_every_handoff_write_failure(
    failure_stage: str,
) -> None:
    source_run, scope, _, _ = _records()
    first = _target(tenant_id=source_run.tenant_id)
    second = _target(tenant_id=source_run.tenant_id, name="Final Approver")
    intent = GroupAgentHandoffIntent(
        source_run_id=source_run.id,
        source_agent_id=source_run.agent_id,
        sender_participant_id=scope.participant.id,
        group_id=scope.group.id,
        session_id=scope.session.id,
        child_parent_run_id=source_run.id,
        child_root_run_id=source_run.root_run_id or source_run.id,
        mention_participant_ids=(first.participant_id, second.participant_id),
        trigger_message_id=uuid.uuid5(source_run.id, "rollback-handoff-message"),
        cutoff_created_at=NOW,
        idempotency_key=f"run:{source_run.id}:terminal:completed",
        origin_user_id=source_run.origin_user_id,
        mode="enforced",
        plan_prompt="Review then approve.",
    )
    db = _DB()
    transaction = _RollbackTransaction(db)
    starts = 0

    async def start_run(command: StartRunCommand) -> RunHandle:
        nonlocal starts
        starts += 1
        db.add(("child", command.agent_id))
        if failure_stage == "second_child" and starts == 2:
            raise RuntimeError("second child insert failed")
        run_id = uuid.uuid4()
        return RunHandle(
            tenant_id=source_run.tenant_id,
            run_id=run_id,
            thread_id=str(run_id),
            command_id=uuid.uuid4(),
            runtime_type="langgraph",
            created=True,
        )

    async def persist_message(*args, **kwargs):
        del args, kwargs
        db.add(("message", intent.trigger_message_id))
        if failure_stage == "message":
            raise GroupMessageServiceError(
                "group_message_write_failed",
                "message insert failed",
            )
        raise AssertionError("message persistence should not run in this case")

    with (
        patch(
            "app.services.agent_runtime.group_handoff._load_sender_scope",
            new=AsyncMock(return_value=scope),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._resolve_mentions",
            new=AsyncMock(return_value=(first, second)),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.AgentCycleGuard.ensure_delegation_allowed",
            new=AsyncMock(return_value=_cycle_check()),
        ),
        patch(
            "app.services.agent_runtime.group_handoff.RuntimeCommandIntake.start_run",
            new=AsyncMock(side_effect=start_run),
        ),
        patch(
            "app.services.agent_runtime.group_handoff._persist_message",
            new=AsyncMock(side_effect=persist_message),
        ) as persist,
    ):
        with pytest.raises((RuntimeError, GroupAgentHandoffError)):
            async with transaction:
                await apply_group_agent_handoff(
                    db,  # type: ignore[arg-type]
                    source_run=source_run,
                    content="Public review result",
                    intent_payload=intent.payload(),
                    expected_idempotency_key=intent.idempotency_key,
                    expected_message_id=intent.trigger_message_id,
                    settings=_settings(),
                    clock=lambda: NOW,
                )

    assert transaction.rolled_back is True
    assert db.added == []
    if failure_stage == "second_child":
        persist.assert_not_awaited()
    else:
        persist.assert_awaited_once()
