"""Focused tests for checkpoint-derived Runtime delivery transactions."""

from collections import deque
from datetime import UTC, datetime
import inspect
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.channel_delivery import ChannelDelivery
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services.agent_runtime.delivery import (
    DeliveryRequest,
    DeliveryServiceError,
    deliver_runtime_message,
)


NOW = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)


class _Result:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RecordingDB:
    def __init__(self, *values) -> None:
        self.results = deque(_Result(value) for value in values)
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.popleft()

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        raise AssertionError("delivery service must not commit the caller transaction")

    async def rollback(self) -> None:
        raise AssertionError("delivery service must not roll back the caller transaction")


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _agent(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    return Agent(
        id=agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Delivery Agent",
        avatar_url="agent.png",
        status="idle",
    )


def _participant(agent_id: uuid.UUID) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=agent_id,
        display_name="Delivery Agent",
        avatar_url="agent.png",
    )


def _user(tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    return User(
        id=user_id,
        tenant_id=tenant_id,
        display_name="Runtime User",
        role="member",
        is_active=True,
    )


def _group(tenant_id: uuid.UUID, group_id: uuid.UUID) -> Group:
    return Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Runtime Group",
        created_by_participant_id=uuid.uuid4(),
        deleted_at=None,
    )


def _session(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    deleted: bool = False,
    primary: bool = False,
) -> ChatSession:
    is_group = group_id is not None
    return ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group" if is_group else "direct",
        group_id=group_id,
        agent_id=agent_id,
        user_id=user_id,
        created_by_participant_id=uuid.uuid4(),
        title="Runtime Session",
        source_channel="web",
        is_group=is_group,
        is_primary=primary,
        deleted_at=NOW if deleted else None,
        last_message_at=None,
    )


def _run(
    *,
    tenant_id: uuid.UUID,
    session: ChatSession | None,
    agent_id: uuid.UUID | None,
    run_kind: str = "foreground",
    system_role: str | None = None,
    source_type: str = "chat",
    delivery_target: dict | None = None,
    origin_user_id: uuid.UUID | None = None,
) -> AgentRun:
    run_id = uuid.uuid4()
    return AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session.id if session is not None else None,
        source_type=source_type,
        origin_user_id=origin_user_id,
        goal="Deliver the Runtime result",
        run_kind=run_kind,
        system_role=system_role,
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
        delivery_target=delivery_target,
    )


def _terminal_request(
    run: AgentRun,
    *,
    status: str = "completed",
    content: str = "Done",
    original_target_outcome: str = "not_attempted",
) -> DeliveryRequest:
    return DeliveryRequest(
        tenant_id=run.tenant_id,
        run_id=run.id,
        kind="terminal",
        content=content,
        checkpoint_id="checkpoint-terminal",
        lifecycle_status=status,  # type: ignore[arg-type]
        original_target_outcome=original_target_outcome,  # type: ignore[arg-type]
    )


def _added(db: _RecordingDB, model_type):
    return [value for value in db.added if isinstance(value, model_type)]


def test_delivery_request_uses_the_documented_stable_keys() -> None:
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    ack = DeliveryRequest(
        tenant_id=tenant_id,
        run_id=run_id,
        kind="ack",
        content="Accepted",
    )
    waiting = DeliveryRequest(
        tenant_id=tenant_id,
        run_id=run_id,
        kind="waiting",
        content="Please confirm",
        checkpoint_id="checkpoint-waiting",
        lifecycle_status="waiting_user",
        interrupt_id="interrupt-7",
    )
    terminal = DeliveryRequest(
        tenant_id=tenant_id,
        run_id=run_id,
        kind="terminal",
        content="Done",
        checkpoint_id="checkpoint-terminal",
        lifecycle_status="completed",
    )

    assert ack.idempotency_key == f"run:{run_id}:ack"
    assert waiting.idempotency_key == f"run:{run_id}:waiting:interrupt-7"
    assert terminal.idempotency_key == f"run:{run_id}:terminal:completed"


@pytest.mark.asyncio
async def test_group_terminal_delivery_is_one_transaction_with_agent_identity() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
    )
    run = _run(tenant_id=tenant_id, session=session, agent_id=agent_id)
    agent = _agent(tenant_id, agent_id)
    participant = _participant(agent_id)
    membership = GroupMember(
        group_id=group_id,
        participant_id=participant.id,
        role="member",
        removed_at=None,
    )
    db = _RecordingDB(
        run,
        None,
        session,
        agent,
        participant,
        _group(tenant_id, group_id),
        membership,
    )
    request = _terminal_request(run, content="Public result")

    receipt = await deliver_runtime_message(db, request, clock=lambda: NOW)

    assert receipt.status == "delivered"
    assert receipt.idempotency_key == f"run:{run.id}:terminal:completed"
    assert receipt.actual_session_id == session.id
    assert receipt.requested_session_id == session.id
    assert run.delivery_status == "delivered"
    assert session.last_message_at == NOW
    messages = _added(db, ChatMessage)
    events = _added(db, AgentRunEvent)
    assert len(messages) == len(events) == 1
    message = messages[0]
    assert message.id == receipt.message_id
    assert message.role == "assistant"
    assert message.participant_id == participant.id
    assert message.agent_id == agent_id
    assert message.user_id is None
    assert message.conversation_id == str(session.id)
    assert message.content == "Public result"
    event = events[0]
    assert event.event_type == "delivery_succeeded"
    assert event.idempotency_key == request.idempotency_key
    assert event.source_checkpoint_id == "checkpoint-terminal"
    assert event.payload["message_id"] == str(message.id)
    assert event.payload["requested_target"]["session_id"] == str(session.id)
    assert event.payload["actual_target"]["group_id"] == str(group_id)
    assert db.flush_count == 1

    run_sql = _sql(db.statements[0])
    assert f"agent_runs.tenant_id = '{tenant_id}'" in run_sql
    assert f"agent_runs.id = '{run.id}'" in run_sql
    assert "FOR UPDATE" in run_sql
    membership_sql = _sql(db.statements[-1])
    assert f"group_members.group_id = '{group_id}'" in membership_sql
    assert f"group_members.participant_id = '{participant.id}'" in membership_sql
    assert "group_members.removed_at IS NULL" in membership_sql


@pytest.mark.asyncio
async def test_external_group_delivery_uses_channel_scope_without_native_membership() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    sender_user_id = uuid.uuid4()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=None,
        agent_id=agent_id,
        user_id=uuid.uuid4(),
        created_by_participant_id=uuid.uuid4(),
        title="Feishu Group",
        source_channel="feishu",
        external_conv_id="feishu_group_oc_123",
        is_group=True,
        is_primary=False,
        deleted_at=None,
    )
    run = _run(
        tenant_id=tenant_id,
        session=session,
        agent_id=agent_id,
        origin_user_id=sender_user_id,
        delivery_target={
            "kind": "session",
            "session_id": str(session.id),
            "channel_delivery": {
                "version": 1,
                "channel": "feishu",
                "target": {
                    "receive_id": "oc_123",
                    "receive_id_type": "chat_id",
                },
            },
        },
    )
    participant = _participant(agent_id)
    db = _RecordingDB(
        run,
        None,
        session,
        _agent(tenant_id, agent_id),
        participant,
    )

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run, content="External group result"),
        clock=lambda: NOW,
    )

    assert receipt.status == "delivered"
    message = _added(db, ChatMessage)[0]
    assert message.conversation_id == str(session.id)
    assert message.participant_id == participant.id
    assert message.user_id is None
    outbox = _added(db, ChannelDelivery)
    assert len(outbox) == 1
    assert outbox[0].message_id == message.id
    assert outbox[0].channel == "feishu"
    assert outbox[0].target["receive_id"] == "oc_123"
    assert run.delivery_status == "pending"
    assert len(db.statements) == 5


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_the_stored_receipt_without_a_message() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    run = _run(tenant_id=tenant_id, session=session, agent_id=agent_id)
    request = _terminal_request(run)
    message_id = uuid.uuid4()
    event = AgentRunEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run.id,
        agent_id=agent_id,
        event_type="delivery_succeeded",
        summary="Runtime delivery succeeded",
        payload={
            "version": 1,
            "status": "delivered",
            "delivery_kind": "terminal",
            "checkpoint_id": "checkpoint-terminal",
            "message_id": str(message_id),
            "requested_session_id": str(session.id),
            "actual_session_id": str(session.id),
            "fallback_reason": None,
            "error_code": None,
        },
        artifact_refs=[],
        idempotency_key=request.idempotency_key,
        source_checkpoint_id="checkpoint-terminal",
    )
    db = _RecordingDB(run, event)

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run, content="A different retry payload"),
        clock=lambda: NOW,
    )

    assert receipt.message_id == message_id
    assert receipt.actual_session_id == session.id
    assert receipt.idempotency_key == request.idempotency_key
    assert db.added == []
    assert db.flush_count == 0
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_foreground_deleted_session_fails_without_primary_fallback() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    deleted = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        deleted=True,
    )
    run = _run(tenant_id=tenant_id, session=deleted, agent_id=agent_id)
    db = _RecordingDB(run, None, deleted)

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run),
        clock=lambda: NOW,
    )

    assert receipt.status == "failed"
    assert receipt.error_code == "original_session_unavailable"
    assert receipt.fallback_reason == "requested_session_deleted"
    assert receipt.actual_session_id is None
    assert run.delivery_status == "failed"
    assert _added(db, ChatMessage) == []
    assert _added(db, AgentRunEvent)[0].event_type == "delivery_failed"
    assert len(db.statements) == 3


@pytest.mark.asyncio
async def test_background_direct_falls_back_to_same_scope_primary_before_first_write() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    deleted = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        deleted=True,
    )
    primary = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        primary=True,
    )
    run = _run(
        tenant_id=tenant_id,
        session=deleted,
        agent_id=agent_id,
        run_kind="background",
        source_type="trigger",
        delivery_target={
            "kind": "session",
            "session_id": str(deleted.id),
            "owner_user_id": str(user_id),
        },
    )
    participant = _participant(agent_id)
    db = _RecordingDB(
        run,
        None,
        deleted,
        primary,
        _agent(tenant_id, agent_id),
        participant,
        _user(tenant_id, user_id),
    )

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run, content="Background result"),
        clock=lambda: NOW,
    )

    assert receipt.status == "delivered"
    assert receipt.requested_session_id == deleted.id
    assert receipt.actual_session_id == primary.id
    assert receipt.fallback_reason == "requested_session_deleted"
    message = _added(db, ChatMessage)[0]
    assert message.conversation_id == str(primary.id)
    assert message.user_id == user_id
    primary_sql = _sql(db.statements[3])
    assert f"chat_sessions.tenant_id = '{tenant_id}'" in primary_sql
    assert f"chat_sessions.agent_id = '{agent_id}'" in primary_sql
    assert f"chat_sessions.user_id = '{user_id}'" in primary_sql
    event = _added(db, AgentRunEvent)[0]
    assert event.payload["requested_target"]["session_id"] == str(deleted.id)
    assert event.payload["actual_target"]["session_id"] == str(primary.id)
    assert event.payload["fallback_reason"] == "requested_session_deleted"


@pytest.mark.asyncio
async def test_background_group_fallback_stays_in_the_original_group() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    group_id = uuid.uuid4()
    deleted = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
        deleted=True,
    )
    primary = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
        primary=True,
    )
    run = _run(
        tenant_id=tenant_id,
        session=deleted,
        agent_id=agent_id,
        run_kind="background",
        source_type="task",
        delivery_target={
            "kind": "group",
            "session_id": str(deleted.id),
            "group_id": str(group_id),
        },
    )
    participant = _participant(agent_id)
    membership = GroupMember(
        group_id=group_id,
        participant_id=participant.id,
        role="member",
        removed_at=None,
    )
    db = _RecordingDB(
        run,
        None,
        deleted,
        primary,
        _agent(tenant_id, agent_id),
        participant,
        _group(tenant_id, group_id),
        membership,
    )

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run),
        clock=lambda: NOW,
    )

    assert receipt.actual_session_id == primary.id
    assert receipt.fallback_reason == "requested_session_deleted"
    fallback_sql = _sql(db.statements[3])
    assert f"chat_sessions.group_id = '{group_id}'" in fallback_sql
    assert f"chat_sessions.tenant_id = '{tenant_id}'" in fallback_sql
    assert "chat_sessions.is_primary IS true" in fallback_sql
    assert "chat_sessions.deleted_at IS NULL" in fallback_sql


@pytest.mark.asyncio
async def test_unknown_original_outcome_never_switches_to_a_primary() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    deleted = _session(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        deleted=True,
    )
    run = _run(
        tenant_id=tenant_id,
        session=deleted,
        agent_id=agent_id,
        run_kind="background",
        source_type="heartbeat",
        delivery_target={
            "kind": "session",
            "session_id": str(deleted.id),
            "owner_user_id": str(user_id),
        },
    )
    db = _RecordingDB(run, None, deleted)

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run, original_target_outcome="unknown"),
        clock=lambda: NOW,
    )

    assert receipt.status == "failed"
    assert receipt.error_code == "original_target_outcome_unknown"
    assert receipt.fallback_reason == "requested_session_deleted"
    assert len(db.statements) == 3
    assert _added(db, ChatMessage) == []


@pytest.mark.asyncio
async def test_planning_failure_uses_system_identity_and_redacted_content() -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
    )
    run = _run(
        tenant_id=tenant_id,
        session=session,
        agent_id=None,
        run_kind="orchestration",
        system_role="group_planning",
    )
    db = _RecordingDB(run, None, session, _group(tenant_id, group_id))

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(
            run,
            status="failed",
            content="postgres://admin:secret@db /private/path traceback",
        ),
        clock=lambda: NOW,
    )

    assert receipt.status == "delivered"
    assert run.delivery_status == "delivered"
    message = _added(db, ChatMessage)[0]
    assert message.role == "system"
    assert message.agent_id is None
    assert message.participant_id is None
    assert message.content == "任务规划未完成，请重试或改为单 Agent 处理。"
    assert "secret" not in message.content
    assert "traceback" not in message.content
    assert _added(db, AgentRunEvent)[0].event_type == "delivery_succeeded"


@pytest.mark.asyncio
async def test_planning_child_failure_is_reported_as_execution_failure() -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
    )
    run = _run(
        tenant_id=tenant_id,
        session=session,
        agent_id=None,
        run_kind="orchestration",
        system_role="group_planning",
    )
    run.projected_error_code = "planning_child_failed"
    db = _RecordingDB(run, None, session, _group(tenant_id, group_id))

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run, status="failed", content="sensitive child error"),
        clock=lambda: NOW,
    )

    assert receipt.status == "delivered"
    message = _added(db, ChatMessage)[0]
    assert message.role == "system"
    assert message.content == "任务执行未完成：一个或多个 Agent 子任务失败，请重试。"
    assert "sensitive" not in message.content


@pytest.mark.asyncio
async def test_removed_group_agent_fails_without_writing_a_message() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = _session(
        tenant_id=tenant_id,
        agent_id=None,
        group_id=group_id,
    )
    run = _run(tenant_id=tenant_id, session=session, agent_id=agent_id)
    participant = _participant(agent_id)
    db = _RecordingDB(
        run,
        None,
        session,
        _agent(tenant_id, agent_id),
        participant,
        _group(tenant_id, group_id),
        None,
    )

    receipt = await deliver_runtime_message(
        db,
        _terminal_request(run),
        clock=lambda: NOW,
    )

    assert receipt.status == "failed"
    assert receipt.error_code == "agent_not_group_member"
    assert _added(db, ChatMessage) == []
    assert _added(db, AgentRunEvent)[0].event_type == "delivery_failed"


@pytest.mark.asyncio
async def test_waiting_delivery_rejects_non_user_waiting_checkpoint() -> None:
    request = DeliveryRequest(
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        kind="waiting",
        content="Internal wait",
        checkpoint_id="checkpoint-waiting",
        lifecycle_status="waiting_external",  # type: ignore[arg-type]
        interrupt_id="interrupt-1",
    )
    db = _RecordingDB()

    with pytest.raises(DeliveryServiceError) as exc_info:
        await deliver_runtime_message(db, request)

    assert exc_info.value.code == "invalid_delivery_request"
    assert db.statements == []
    assert db.added == []
    assert "projected_" not in inspect.getsource(deliver_runtime_message)
