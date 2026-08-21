"""Direct Session runtime-state must resolve one scoped lane holder exactly."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException
import pytest

from app.api import chat_sessions as chat_sessions_api
from app.api.chat_sessions import get_session_runtime_state
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_tool_execution import AgentToolExecution
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services.agent_runtime.contracts import RunView
from app.services.agent_runtime.run_state_reader import RunStateReadError


class _Scalars:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class _Result:
    def __init__(self, *, scalar: object = None, values: list[object] | None = None) -> None:
        self._scalar = scalar
        self._values = values or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)


class _Session:
    def __init__(self, *results: _Result) -> None:
        self.results = deque(results)

    async def execute(self, _statement):
        return self.results.popleft()


class _WritableSession(_Session):
    def __init__(self, *results: _Result) -> None:
        super().__init__(*results)
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class _ReaderContext:
    def __init__(self, reader: object) -> None:
        self.reader = reader

    async def __aenter__(self):
        return self.reader

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _records() -> tuple[Agent, User, ChatSession, AgentRun]:
    tenant_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Ada",
        role="member",
        is_active=True,
    )
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=user.id,
        name="Analyst",
        status="idle",
        agent_type="native",
    )
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="direct",
        agent_id=agent.id,
        user_id=user.id,
        title="Direct",
        source_channel="web",
        is_group=False,
        is_primary=True,
    )
    now = datetime(2026, 7, 16, 18, 30, tzinfo=UTC)
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        session_id=session.id,
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id=str(session.id),
        graph_name="runtime_graph",
        graph_version="v1",
        scheduling_lane_key=f"direct_chat_thread:{tenant_id}:{session.id}",
        scheduling_position_created_at=now,
        scheduling_position_id=uuid.uuid4(),
        lane_held=True,
        delivery_status="delivered",
        origin_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    return agent, user, session, run


def _view(run: AgentRun, *, status: str = "waiting_user") -> RunView:
    return RunView(
        tenant_id=run.tenant_id,
        run_id=run.id,
        thread_id=run.runtime_thread_id,
        session_id=run.session_id,
        source_type="chat",
        run_kind="foreground",
        goal=run.goal,
        runtime_type="langgraph",
        execution_status=status,  # type: ignore[arg-type]
        current_node="wait" if status == "waiting_user" else "model",
        model_step_count=2,
        waiting_type="user" if status == "waiting_user" else None,
        waiting_reason="Continue?" if status == "waiting_user" else None,
        waiting_correlation_id="confirm-1" if status == "waiting_user" else None,
        result_summary=None,
        error_code=None,
        last_error=None,
        verification_result=None,
        delivery_status="delivered",
        applied_checkpoint_id="checkpoint-1",
        checkpoint_created_at=run.updated_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@pytest.mark.asyncio
async def test_runtime_state_returns_exact_waiting_lane_holder() -> None:
    agent, user, session, run = _records()
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=_view(run)))
    db = _Session(
        _Result(scalar=session),
        _Result(values=[run]),
        _Result(scalar=None),
        _Result(values=[]),
        _Result(scalar=None),
    )

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        response = await get_session_runtime_state(
            agent.id,
            session.id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.active_run is not None
    assert response.active_run.run_id == str(run.id)
    assert response.active_run.status == "waiting_user"
    assert response.active_run.correlation_id == "confirm-1"
    assert response.active_run.can_resume is True
    assert response.active_run.can_cancel is True
    reader.get_run_state.assert_awaited_once_with(run.tenant_id, run.id)


@pytest.mark.asyncio
async def test_runtime_state_exposes_unknown_write_and_blocks_plain_resume() -> None:
    agent, user, session, run = _records()
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=_view(run)))
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        tool_call_id="call-write-1",
        tool_name="write_file",
        assistant_message_id="assistant-1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect="write",
        retry_policy="conditional",
        attempt_count=1,
        status="unknown",
        result_summary="Workspace write outcome is unknown.",
        result_metadata={"error_code": "workspace_write_outcome_unknown"},
        started_at=run.created_at,
        completed_at=run.updated_at,
    )
    db = _Session(
        _Result(scalar=session),
        _Result(values=[run]),
        _Result(scalar=None),
        _Result(values=[execution]),
        _Result(scalar=None),
    )

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        response = await get_session_runtime_state(
            agent.id,
            session.id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.active_run is not None
    assert response.active_run.can_resume is False
    assert len(response.active_run.pending_tool_reconciliations) == 1
    pending = response.active_run.pending_tool_reconciliations[0]
    assert pending.execution_id == str(execution.id)
    assert pending.tool_name == "write_file"
    assert pending.can_reconcile is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "contract_version"),
    [
        ("execute_code", None),
        ("execute_code_e2b", None),
        ("generate_image_openai", None),
        ("tenant_search", "registered:tenant_search:0123456789abcdef"),
    ],
)
async def test_runtime_state_exposes_reconcilable_unknown_tool_for_user_confirmation(
    tool_name: str,
    contract_version: str | None,
) -> None:
    agent, user, session, run = _records()
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=_view(run)))
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        tool_call_id="call-image-1",
        tool_name=tool_name,
        contract_version=contract_version,
        assistant_message_id="assistant-1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect="external_write",
        retry_policy="never",
        attempt_count=1,
        status="unknown",
        result_summary="The image generation outcome is unknown.",
        result_metadata={"error_code": "image_generation_outcome_unknown"},
        started_at=run.created_at,
        completed_at=run.updated_at,
    )
    db = _Session(
        _Result(scalar=session),
        _Result(values=[run]),
        _Result(scalar=None),
        _Result(values=[execution]),
        _Result(scalar=None),
    )

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        response = await get_session_runtime_state(
            agent.id,
            session.id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.active_run is not None
    assert response.active_run.can_resume is False
    assert response.active_run.pending_tool_reconciliations[0].tool_name == tool_name
    assert response.active_run.pending_tool_reconciliations[0].can_reconcile is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "settled_status"),
    [("applied", "succeeded"), ("not_applied", "failed")],
)
async def test_direct_code_reconciliation_resumes_same_run(
    outcome: str,
    settled_status: str,
) -> None:
    agent, user, session, run = _records()
    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        tool_call_id="call-code-1",
        tool_name="execute_code",
        assistant_message_id="assistant-1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect="external_write",
        retry_policy="never",
        attempt_count=1,
        status="unknown",
        result_summary="Code outcome is unknown.",
        result_metadata={"error_code": "tool_deadline_outcome_unknown"},
        started_at=run.created_at,
        completed_at=run.updated_at,
    )
    db = _WritableSession(
        _Result(scalar=session),
        _Result(scalar=run),
        _Result(scalar=execution),
    )
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=_view(run)))
    resume_commands: list[object] = []

    async def fake_access(_db, _user, _agent_id):
        return agent, run.tenant_id

    async def fake_reconcile(_db, **kwargs):
        assert kwargs["execution_id"] == execution.id
        assert kwargs["confirmed_status"] == settled_status
        assert kwargs["resolution_action"] == outcome
        execution.status = settled_status
        execution.result_summary = "settled"
        execution.result_metadata = {
            **execution.result_metadata,
            "external_reconciliation": True,
            "workspace_resolution_action": outcome,
        }
        return execution

    class _RuntimeIntake:
        def __init__(self, _db):
            pass

        async def resume_run(self, command):
            resume_commands.append(command)
            return SimpleNamespace()

    body = chat_sessions_api.ReconcileToolExecutionIn(
        outcome=outcome,
        correlation_id="confirm-1",
        note="verified by operator",
    )
    with (
        patch(
            "app.api.chat_sessions._check_direct_agent_access",
            new=fake_access,
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
        patch(
            "app.api.chat_sessions.reconcile_unknown_tool_execution",
            new=fake_reconcile,
        ),
        patch(
            "app.api.chat_sessions.RuntimeCommandIntake",
            new=_RuntimeIntake,
        ),
    ):
        response = await chat_sessions_api.reconcile_direct_tool_execution(
            agent.id,
            session.id,
            run.id,
            execution.id,
            body,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.status == settled_status
    assert response.result_summary == "settled"
    assert db.commits == 1
    assert len(resume_commands) == 1
    resume = resume_commands[0]
    assert resume.run_id == run.id
    assert resume.payload["resume_type"] == "tool_reconciliation"
    assert resume.payload["correlation_id"] == "confirm-1"
    assert resume.payload["payload"]["tool_execution_id"] == str(execution.id)
    assert "workspace_resolution_action" not in resume.payload["payload"]


@pytest.mark.asyncio
async def test_runtime_state_disables_resume_and_cancel_while_cancel_is_inflight() -> None:
    agent, user, session, run = _records()
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=_view(run)))
    cancel = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="cancel",
        payload={"reason": "cancelled_by_user"},
        actor_user_id=user.id,
        idempotency_key=f"cancel:web:{run.id}",
        status="pending",
        attempt_count=0,
        created_at=run.updated_at,
    )
    db = _Session(
        _Result(scalar=session),
        _Result(values=[run]),
        _Result(scalar=None),
        _Result(values=[]),
        _Result(scalar=cancel.id),
    )

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        response = await get_session_runtime_state(
            agent.id,
            session.id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.active_run is not None
    assert response.active_run.status == "waiting_user"
    assert response.active_run.can_resume is False
    assert response.active_run.can_cancel is False


@pytest.mark.asyncio
async def test_runtime_state_has_null_active_run_without_lane_holder() -> None:
    agent, user, session, _run = _records()
    db = _Session(_Result(scalar=session), _Result(values=[]))

    with patch(
        "app.api.chat_sessions.check_agent_access",
        new=AsyncMock(return_value=(agent, None)),
    ):
        response = await get_session_runtime_state(
            agent.id,
            session.id,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response.active_run is None


@pytest.mark.asyncio
async def test_runtime_state_rejects_wrong_user_session_scope() -> None:
    agent, user, session, _run = _records()
    session.user_id = uuid.uuid4()
    db = _Session(_Result(scalar=None))

    with patch(
        "app.api.chat_sessions.check_agent_access",
        new=AsyncMock(return_value=(agent, None)),
    ):
        with pytest.raises(HTTPException) as raised:
            await get_session_runtime_state(
                agent.id,
                session.id,
                current_user=user,
                db=db,  # type: ignore[arg-type]
            )

    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_runtime_state_fails_closed_for_multiple_lane_holders() -> None:
    agent, user, session, run = _records()
    other = SimpleNamespace(**{key: value for key, value in vars(run).items() if not key.startswith("_")})
    other.id = uuid.uuid4()
    db = _Session(_Result(scalar=session), _Result(values=[run, other]))

    with patch(
        "app.api.chat_sessions.check_agent_access",
        new=AsyncMock(return_value=(agent, None)),
    ):
        with pytest.raises(HTTPException) as raised:
            await get_session_runtime_state(
                agent.id,
                session.id,
                current_user=user,
                db=db,  # type: ignore[arg-type]
            )

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_runtime_state_fails_closed_when_reader_identity_disagrees() -> None:
    agent, user, session, run = _records()
    wrong = _view(run)
    object.__setattr__(wrong, "session_id", uuid.uuid4())
    reader = SimpleNamespace(get_run_state=AsyncMock(return_value=wrong))
    db = _Session(_Result(scalar=session), _Result(values=[run]))

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            await get_session_runtime_state(
                agent.id,
                session.id,
                current_user=user,
                db=db,  # type: ignore[arg-type]
            )

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_runtime_state_maps_reader_failure_to_fail_closed_response() -> None:
    agent, user, session, run = _records()
    reader = SimpleNamespace(
        get_run_state=AsyncMock(
            side_effect=RunStateReadError("inconsistent_checkpoint", "bad snapshot")
        )
    )
    db = _Session(_Result(scalar=session), _Result(values=[run]))

    with (
        patch(
            "app.api.chat_sessions.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.chat_sessions._open_run_state_reader",
            return_value=_ReaderContext(reader),
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            await get_session_runtime_state(
                agent.id,
                session.id,
                current_user=user,
                db=db,  # type: ignore[arg-type]
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "inconsistent_checkpoint"
