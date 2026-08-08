"""Transactional Runtime A2A intake and replay contract tests."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import ChatMessage
from app.models.gateway_message import GatewayMessage
from app.services.agent_runtime.a2a_runtime import (
    A2ARuntimeError,
    RuntimeA2AService,
    _request,
    _resolve_target,
    a2a_mode_from_correlation,
    a2a_waiting_request,
    complete_gateway_a2a_runtime,
    enqueue_gateway_a2a_runtime,
)
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.cycle_guard import AgentCycleGuardError
from app.services.agent_runtime.tool_execution import ToolExecutionReservation


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else [self.value]


class _Transaction:
    def __init__(self, db: "_Session") -> None:
        self.db = db

    async def __aenter__(self):
        assert not self.db.in_transaction
        self.db.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.db.in_transaction = False
        return False


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.in_transaction = False
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.results.popleft())

    async def get(self, _model, _identity):
        return None

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
    def __init__(self, error: AgentCycleGuardError | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def ensure_delegation_allowed(self, db, **kwargs):
        assert db.in_transaction
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(cycle_count=0)


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=False,
        AGENT_RUNTIME_V2_SOURCE_TYPES="a2a" if enabled else "",
    )


def _records() -> tuple[uuid.UUID, Agent, Agent, AgentRun, ToolExecutionReservation]:
    tenant_id = uuid.uuid4()
    source = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Coordinator",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
        agent_type="native",
    )
    target = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Researcher",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
        agent_type="native",
    )
    source_run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=source.id,
        source_type="chat",
        source_id=str(uuid.uuid4()),
        goal="Coordinate the answer",
        run_kind="foreground",
        model_id=source.primary_model_id,
        runtime_type="langgraph",
        runtime_thread_id="source-thread",
        graph_name="runtime",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
        origin_user_id=source.creator_id,
    )
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=source_run.id,
        tool_call_id="delegate-call",
        tool_name="send_message_to_agent",
        assistant_message_id="assistant-message",
        arguments_hash="hash",
        sanitized_arguments={},
        status="started",
        lease_owner="runtime:command:delegate-call",
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
    return tenant_id, source, target, source_run, reservation


def test_directory_target_id_is_the_primary_runtime_a2a_contract() -> None:
    target_id = uuid.uuid4()

    request = _request(
        {
            "target_agent_id": str(target_id),
            "message": "Check the facts",
            "msg_type": "consult",
        }
    )

    assert request.target_agent_id == target_id
    assert request.target_name is None

    with pytest.raises(A2ARuntimeError) as raised:
        _request(
            {
                "target_agent_id": "not-a-uuid",
                "message": "Check the facts",
            }
        )
    assert raised.value.code == "a2a_target_id_invalid"


@pytest.mark.asyncio
async def test_directory_company_target_does_not_require_legacy_relationship() -> None:
    _, source, target, _, _ = _records()
    source.access_mode = "company"
    target.access_mode = "company"
    db = _Session(target)

    resolved = await _resolve_target(
        db,  # type: ignore[arg-type]
        source_agent=source,
        target_agent_id=target.id,
        target_name=None,
        actor_user_id=source.creator_id,
    )

    assert resolved is target


@pytest.mark.asyncio
async def test_gateway_message_and_native_target_run_are_accepted_atomically() -> None:
    tenant_id = uuid.uuid4()
    source = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="OpenClaw Coordinator",
        status="running",
        is_expired=False,
        agent_type="openclaw",
    )
    target = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Native Researcher",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
        agent_type="native",
    )
    db = _Session()
    session = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=min((source.id, target.id), key=str),
        last_message_at=None,
    )
    source_participant_id = uuid.uuid4()
    message_id = uuid.uuid4()
    target_run_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=target_run_id,
        thread_id=str(target_run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with (
        patch(
            "app.services.agent_runtime.a2a_runtime.ensure_a2a_session",
            new=AsyncMock(
                return_value=(session, source_participant_id, uuid.uuid4())
            ),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        intake = await enqueue_gateway_a2a_runtime(
            db,  # type: ignore[arg-type]
            source_agent=source,
            target_agent=target,
            content="Research the incident",
            message_id=message_id,
            settings=_settings(enabled=True),
        )

    assert intake is not None
    assert intake.gateway_message_id == message_id
    assert intake.target_run_id == target_run_id
    assert intake.session_id == session.id
    inbound = next(value for value in db.added if isinstance(value, GatewayMessage))
    assert inbound.agent_id == target.id
    assert inbound.sender_agent_id == source.id
    assert inbound.status == "delivered"
    chat_message = next(value for value in db.added if isinstance(value, ChatMessage))
    assert chat_message.id == uuid.uuid5(message_id, "gateway-a2a-input")
    assert chat_message.content == "Research the incident"
    assert chat_message.participant_id == source_participant_id
    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.source_execution_id == f"gateway-a2a:{message_id}"
    assert command.origin_agent_id == source.id
    assert command.payload["gateway_message_id"] == str(message_id)
    assert command.payload["gateway_reply_agent_id"] == str(source.id)
    assert command.payload["input_content"] == "Research the incident"
    assert "a2a_message" not in command.payload


@pytest.mark.asyncio
async def test_gateway_native_target_fails_closed_when_a2a_runtime_is_disabled() -> None:
    tenant_id = uuid.uuid4()
    source = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="OpenClaw Coordinator",
        status="running",
        is_expired=False,
        agent_type="openclaw",
    )
    target = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Native Researcher",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
        agent_type="native",
    )
    db = _Session()

    intake = await enqueue_gateway_a2a_runtime(
        db,  # type: ignore[arg-type]
        source_agent=source,
        target_agent=target,
        content="Research the incident",
        settings=_settings(enabled=False),
    )

    assert intake is None
    assert db.added == []


@pytest.mark.asyncio
async def test_delegate_creates_target_run_and_receipt_in_one_transaction() -> None:
    tenant_id, source, target, source_run, reservation = _records()
    db = _Session(source_run, source)
    cycle_guard = _CycleGuard()
    session = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=min((source.id, target.id), key=str),
        last_message_at=None,
    )
    source_participant_id = uuid.uuid4()
    target_run_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=target_run_id,
        thread_id=str(target_run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    async def mark_succeeded(mark_db, **kwargs):
        assert mark_db is db
        assert db.in_transaction
        reservation.execution.status = "succeeded"
        reservation.execution.result_summary = kwargs["result_summary"]
        reservation.execution.result_ref = kwargs["result_ref"]
        return reservation.execution

    with (
        patch(
            "app.services.agent_runtime.a2a_runtime._resolve_target",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.ensure_a2a_session",
            new=AsyncMock(
                return_value=(session, source_participant_id, uuid.uuid4())
            ),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_succeeded",
            new=AsyncMock(side_effect=mark_succeeded),
        ),
    ):
        result = await RuntimeA2AService(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
            settings=_settings(enabled=True),
            cycle_guard=cycle_guard,  # type: ignore[arg-type]
        ).execute(
            tenant_id=tenant_id,
            source_run_id=source_run.id,
            source_agent_id=source.id,
            tool_call_id="delegate-call",
            arguments={
                "target_agent_id": str(target.id),
                "message": "Research the latest facts",
                "msg_type": "task_delegate",
            },
            reservation=reservation,
            lease_owner="runtime:command:delegate-call",
            actor_user_id=source.creator_id,
        )

    assert result is not None
    assert result.target_run_id == target_run_id
    assert result.outcome.status == "succeeded"
    assert result.outcome.result_ref == f"agent-run:{target_run_id}"
    assert result.waiting_request == {
        "waiting_type": "agent",
        "correlation_id": (
            f"a2a:task_delegate:"
            f"{uuid.uuid5(source_run.id, 'a2a-result:delegate-call')}"
        ),
        "reason": "waiting_for_task_delegate",
        "target_run_id": str(target_run_id),
    }
    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.agent_id == target.id
    assert command.parent_run_id == source_run.id
    assert command.root_run_id == source_run.id
    assert command.origin_agent_id == source.id
    assert command.run_kind == "delegated"
    assert command.source_type == "a2a"
    assert command.session_id == session.id
    assert command.model_id == target.primary_model_id
    assert "runtime_instruction" in command.payload
    assert "automatically" in command.payload["runtime_instruction"]
    assert "send_message_to_agent" in command.payload["runtime_instruction"]
    assert command.payload["message_id"] == str(
        uuid.uuid5(source_run.id, "a2a-input:delegate-call")
    )
    assert command.payload["input_content"] == "Research the latest facts"
    assert "a2a_message" not in command.payload
    assert cycle_guard.calls[0]["source_run_id"] == source_run.id
    messages = [value for value in db.added if isinstance(value, ChatMessage)]
    assert len(messages) == 1
    assert messages[0].content == "Research the latest facts"
    assert messages[0].participant_id == source_participant_id
    assert db.in_transaction is False


@pytest.mark.asyncio
async def test_disabled_native_target_fails_closed_and_settles_receipt() -> None:
    tenant_id, source, target, source_run, reservation = _records()
    intake_db = _Session(source_run, source)
    rejection_db = _Session()

    async def mark_failed(mark_db, **kwargs):
        assert mark_db is rejection_db
        reservation.execution.status = "failed"
        reservation.execution.result_summary = kwargs["result_summary"]
        return reservation.execution

    with (
        patch(
            "app.services.agent_runtime.a2a_runtime._resolve_target",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_succeeded",
            new=AsyncMock(),
        ) as mark_succeeded,
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_failed",
            new=AsyncMock(side_effect=mark_failed),
        ) as mark_failed,
    ):
        result = await RuntimeA2AService(
            session_factory=_SessionFactory(intake_db, rejection_db),  # type: ignore[arg-type]
            settings=_settings(enabled=False),
        ).execute(
            tenant_id=tenant_id,
            source_run_id=source_run.id,
            source_agent_id=source.id,
            tool_call_id="delegate-call",
            arguments={
                "agent_name": target.name,
                "message": "Research the latest facts",
                "msg_type": "task_delegate",
            },
            reservation=reservation,
            lease_owner="runtime:command:delegate-call",
            actor_user_id=source.creator_id,
        )

    assert result.outcome.status == "failed"
    assert result.target_run_id is None
    assert "runtime_disabled" in (result.outcome.result_summary or "")
    mark_succeeded.assert_not_awaited()
    mark_failed.assert_awaited_once()
    assert reservation.execution.status == "failed"


@pytest.mark.asyncio
async def test_openclaw_target_is_queued_atomically_without_legacy_executor() -> None:
    tenant_id, source, target, source_run, reservation = _records()
    target.agent_type = "openclaw"
    target.primary_model_id = None
    db = _Session(source_run, source)
    cycle_guard = _CycleGuard()
    session = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=min((source.id, target.id), key=str),
        last_message_at=None,
    )
    source_participant_id = uuid.uuid4()

    async def mark_succeeded(mark_db, **kwargs):
        reservation.execution.status = "succeeded"
        reservation.execution.result_summary = kwargs["result_summary"]
        reservation.execution.result_ref = kwargs["result_ref"]
        return reservation.execution

    with (
        patch(
            "app.services.agent_runtime.a2a_runtime._resolve_target",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.ensure_a2a_session",
            new=AsyncMock(
                return_value=(session, source_participant_id, uuid.uuid4())
            ),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_succeeded",
            new=AsyncMock(side_effect=mark_succeeded),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.RuntimeCommandIntake.start_run",
            new=AsyncMock(),
        ) as start_run,
    ):
        result = await RuntimeA2AService(
            session_factory=_SessionFactory(db),  # type: ignore[arg-type]
            settings=_settings(enabled=False),
            cycle_guard=cycle_guard,  # type: ignore[arg-type]
        ).execute(
            tenant_id=tenant_id,
            source_run_id=source_run.id,
            source_agent_id=source.id,
            tool_call_id="delegate-call",
            arguments={
                "agent_name": target.name,
                "message": "Research the latest facts",
                "msg_type": "consult",
            },
            reservation=reservation,
            lease_owner="runtime:command:delegate-call",
            actor_user_id=source.creator_id,
        )

    start_run.assert_not_awaited()
    gateway_message_id = uuid.uuid5(
        source_run.id,
        "a2a-gateway:delegate-call",
    )
    queued = next(value for value in db.added if isinstance(value, GatewayMessage))
    assert queued.id == gateway_message_id
    assert queued.agent_id == target.id
    assert queued.sender_agent_id == source.id
    assert queued.status == "pending"
    assert result.outcome.result_ref == f"gateway-message:{gateway_message_id}"
    assert result.waiting_request == {
        "waiting_type": "agent",
        "correlation_id": (
            f"a2a:consult:"
            f"{uuid.uuid5(source_run.id, 'a2a-result:delegate-call')}"
        ),
        "reason": "waiting_for_consult",
        "gateway_message_id": str(gateway_message_id),
    }


@pytest.mark.asyncio
async def test_openclaw_report_resumes_native_source_from_tool_receipt() -> None:
    tenant_id, source, target, source_run, reservation = _records()
    target.agent_type = "openclaw"
    target.tenant_id = tenant_id
    reservation.execution.status = "succeeded"
    reservation.execution.sanitized_arguments = {
        "agent_name": target.name,
        "message": "Research the latest facts",
        "msg_type": "task_delegate",
    }
    gateway_message = GatewayMessage(
        id=uuid.uuid4(),
        agent_id=target.id,
        sender_agent_id=source.id,
        content="Research the latest facts",
        status="delivered",
        conversation_id=str(uuid.uuid4()),
    )
    reservation.execution.result_ref = f"gateway-message:{gateway_message.id}"
    db = _Session([reservation.execution], source_run)
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=source_run.id,
        thread_id=str(source_run.id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with patch(
        "app.services.agent_runtime.a2a_runtime.RuntimeCommandIntake.resume_run",
        new=AsyncMock(return_value=handle),
    ) as resume_run:
        completion = await complete_gateway_a2a_runtime(
            db,  # type: ignore[arg-type]
            gateway_message=gateway_message,
            target_agent=target,
            result="Verified research result",
            settings=_settings(enabled=False),
        )

    assert completion is not None
    assert completion.source_run_id == source_run.id
    assert completion.resumed is True
    command = resume_run.await_args.args[0]
    assert command.run_id == source_run.id
    assert command.payload["resume_type"] == "agent_result"
    assert command.payload["payload"]["gateway_message_id"] == str(
        gateway_message.id
    )
    assert command.payload["payload"]["result_summary"] == "Verified research result"


@pytest.mark.asyncio
async def test_cycle_limit_becomes_known_failed_tool_result() -> None:
    tenant_id, source, target, source_run, reservation = _records()
    intake_db = _Session(source_run, source)
    rejection_db = _Session()
    cycle_guard = _CycleGuard(
        AgentCycleGuardError(
            "agent_cycle_limit_reached",
            "candidate delegation reaches the Agent cycle limit",
        )
    )

    async def mark_failed(mark_db, **kwargs):
        assert mark_db is rejection_db
        assert rejection_db.in_transaction
        reservation.execution.status = "failed"
        reservation.execution.result_summary = kwargs["result_summary"]
        return reservation.execution

    with (
        patch(
            "app.services.agent_runtime.a2a_runtime._resolve_target",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.services.agent_runtime.a2a_runtime.mark_tool_execution_failed",
            new=AsyncMock(side_effect=mark_failed),
        ),
    ):
        result = await RuntimeA2AService(
            session_factory=_SessionFactory(intake_db, rejection_db),  # type: ignore[arg-type]
            settings=_settings(enabled=True),
            cycle_guard=cycle_guard,  # type: ignore[arg-type]
        ).execute(
            tenant_id=tenant_id,
            source_run_id=source_run.id,
            source_agent_id=source.id,
            tool_call_id="delegate-call",
            arguments={
                "agent_name": target.name,
                "message": "Research the latest facts",
                "msg_type": "task_delegate",
            },
            reservation=reservation,
            lease_owner="runtime:command:delegate-call",
            actor_user_id=source.creator_id,
        )

    assert result is not None
    assert result.target_run_id is None
    assert result.outcome.status == "failed"
    assert result.outcome.result_summary is not None
    assert "agent_cycle_limit_reached" in result.outcome.result_summary


def test_a2a_receipt_rebuilds_wait_and_validates_correlation() -> None:
    source_run_id = uuid.uuid4()
    target_run_id = uuid.uuid4()
    waiting = a2a_waiting_request(
        source_run_id=source_run_id,
        tool_call_id="consult-call",
        arguments={
            "agent_name": "Researcher",
            "message": "Check one fact",
            "msg_type": "consult",
        },
        result_ref=f"agent-run:{target_run_id}",
    )

    assert waiting is not None
    correlation_id = waiting["correlation_id"]
    assert isinstance(correlation_id, str)
    assert a2a_mode_from_correlation(correlation_id) == "consult"
    with pytest.raises(A2ARuntimeError) as raised:
        a2a_mode_from_correlation("a2a:consult:not-a-uuid")
    assert raised.value.code == "a2a_correlation_invalid"


@pytest.mark.asyncio
async def test_legacy_a2a_executor_fails_closed_without_side_effects() -> None:
    from app.services.agent_tools import _send_message_to_agent

    result = await _send_message_to_agent(
        uuid.uuid4(),
        {
            "agent_name": "Researcher",
            "message": "Check the facts",
            "msg_type": "consult",
        },
    )

    assert "requires a durable Agent Runtime Run" in result
    assert "was not sent" in result
