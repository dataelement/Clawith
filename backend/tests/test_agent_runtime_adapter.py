"""Transactional Runtime command-intake contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.agent_runtime.adapter as runtime_adapter
from app.config import Settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.llm import LLMModel
from app.services.agent_runtime.adapter import (
    RuntimeAdapterError,
    RuntimeCommandIntake,
)
from app.services.agent_runtime.contracts import (
    CancelRunCommand,
    RUNTIME_COMMAND_METADATA_KEY,
    ResumeRunCommand,
    StartRunCommand,
)
from app.services.agent_runtime.persistence import (
    EnqueuedCommand,
    RegisteredRun,
    RunRegistration,
)


class _Result:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=enabled,
        AGENT_RUNTIME_V2_AGENT_IDS="",
        AGENT_RUNTIME_V2_SOURCE_TYPES="",
        AGENT_RUNTIME_GRAPH_NAME="runtime_graph",
        AGENT_RUNTIME_GRAPH_VERSION="v2",
    )


def _session(*results: object | None) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [_Result(result) for result in results]
    return db


def _agent(
    tenant_id: uuid.UUID,
    *,
    agent_id: uuid.UUID | None = None,
    model_turn_limit: object = 50,
) -> Agent:
    agent = Agent(
        id=agent_id or uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Runtime Agent",
        status="idle",
        agent_type="native",
    )
    agent.max_tool_rounds = model_turn_limit  # type: ignore[assignment]
    return agent


def _model(
    tenant_id: uuid.UUID,
    *,
    model_id: uuid.UUID,
    supports_tool_calling: bool | None = True,
) -> LLMModel:
    return LLMModel(
        id=model_id,
        tenant_id=tenant_id,
        provider="ollama",
        model="local-model",
        api_key_encrypted="ollama",
        label="Local model",
        enabled=True,
        supports_tool_calling=supports_tool_calling,
    )


def _run(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    run_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    model_turn_limit: int | None = 50,
    run_kind: str = "foreground",
    system_role: str | None = None,
    graph_name: str = "runtime_graph",
    graph_version: str = "v2",
    source_execution_id: str | None = None,
) -> AgentRun:
    resolved_run_id = run_id or uuid.uuid4()
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    return AgentRun(
        id=resolved_run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="chat",
        source_execution_id=source_execution_id,
        goal="Answer the user",
        run_kind=run_kind,
        system_role=system_role,
        model_id=uuid.uuid4(),
        model_turn_limit=model_turn_limit,
        runtime_type="langgraph",
        runtime_thread_id=thread_id or str(resolved_run_id),
        graph_name=graph_name,
        graph_version=graph_version,
        lane_held=False,
        delivery_status="pending",
        created_at=now,
        updated_at=now,
    )


def _stored_command(run: AgentRun, command_type: str) -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type=command_type,
        payload={},
        idempotency_key=f"{command_type}:1",
        status="pending",
        attempt_count=0,
    )


def _start(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    source_execution_id: str | None = None,
    thread_id: str | None = None,
    requested_model_turn_limit: int | None = None,
) -> StartRunCommand:
    return StartRunCommand(
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="chat",
        source_execution_id=source_execution_id,
        goal="Answer the user",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_thread_id=thread_id,
        requested_model_turn_limit=requested_model_turn_limit,
        idempotency_key="start:message:1",
        payload={"message_id": "message-1"},
        delivery_status="pending",
    )


@pytest.mark.asyncio
async def test_start_pins_agent_budget_thread_and_internal_request_metadata() -> None:
    tenant_id = uuid.uuid4()
    thread_id = str(uuid.uuid4())
    agent = _agent(tenant_id, model_turn_limit=80)
    command = _start(
        tenant_id,
        agent.id,
        thread_id=thread_id,
        requested_model_turn_limit=40,
    )
    run = _run(
        tenant_id=tenant_id,
        agent_id=agent.id,
        thread_id=thread_id,
        model_turn_limit=40,
    )
    start_command = _stored_command(run, "start")
    db = _session(agent, _model(tenant_id, model_id=command.model_id))

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(return_value=RegisteredRun(run, start_command, True)),
    ) as persist:
        handle = await RuntimeCommandIntake(
            db,
            settings=_settings(enabled=True),
        ).start_run(command)

    registration = persist.await_args.args[1]
    assert isinstance(registration, RunRegistration)
    assert registration.model_turn_limit == 40
    assert registration.runtime_thread_id == thread_id
    assert persist.await_args.kwargs["start_payload"] == {
        "message_id": "message-1",
        RUNTIME_COMMAND_METADATA_KEY: {"requested_model_turn_limit": 40},
    }
    assert (handle.run_id, handle.thread_id, handle.command_id) == (
        run.id,
        thread_id,
        start_command.id,
    )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "expected"),
    [(12, 12), (100, 50), (None, 50)],
)
async def test_oneshot_request_can_only_narrow_the_agent_hard_limit(
    requested: int | None,
    expected: int,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id, model_turn_limit=50)
    command = _start(
        tenant_id,
        agent.id,
        requested_model_turn_limit=requested,
    )
    run = _run(
        tenant_id=tenant_id,
        agent_id=agent.id,
        model_turn_limit=expected,
    )
    db = _session(agent, _model(tenant_id, model_id=command.model_id))

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(
            return_value=RegisteredRun(run, _stored_command(run, "start"), True)
        ),
    ) as persist:
        await RuntimeCommandIntake(db, settings=_settings(enabled=True)).start_run(
            command
        )

    assert persist.await_args.args[1].model_turn_limit == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, 0, -1, True])
async def test_missing_or_invalid_agent_budget_fails_without_runtime_fallback(
    invalid: object,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id, model_turn_limit=invalid)
    db = _session(agent)

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(),
    ) as persist:
        with pytest.raises(RuntimeAdapterError) as raised:
            await RuntimeCommandIntake(
                db,
                settings=_settings(enabled=True),
            ).start_run(_start(tenant_id, agent.id))

    assert raised.value.code == "invalid_agent_model_turn_limit"
    persist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supports_tool_calling", "error_code"),
    [
        (None, "model_tool_calling_unverified"),
        (False, "model_tool_calling_unsupported"),
    ],
)
async def test_new_agent_run_fails_before_persistence_without_verified_tool_calling(
    supports_tool_calling: bool | None,
    error_code: str,
) -> None:
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    command = _start(tenant_id, agent.id)
    model = _model(
        tenant_id,
        model_id=command.model_id,
        supports_tool_calling=supports_tool_calling,
    )
    db = _session(agent, model)

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(),
    ) as persist:
        with pytest.raises(RuntimeAdapterError) as raised:
            await RuntimeCommandIntake(
                db,
                settings=_settings(enabled=True),
            ).start_run(command)

    assert raised.value.code == error_code
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_start_reuses_stored_budget_and_graph_without_agent_reload() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    source_execution_id = "chat:message-1"
    run = _run(
        tenant_id=tenant_id,
        agent_id=agent_id,
        thread_id=str(uuid.uuid4()),
        model_turn_limit=12,
        graph_version="v1",
        source_execution_id=source_execution_id,
    )
    db = _session(run)
    command = _start(
        tenant_id,
        agent_id,
        source_execution_id=source_execution_id,
        thread_id=run.runtime_thread_id,
        requested_model_turn_limit=12,
    )

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(
            return_value=RegisteredRun(run, _stored_command(run, "start"), False)
        ),
    ) as persist:
        handle = await RuntimeCommandIntake(
            db,
            settings=_settings(enabled=False),
        ).start_run(command)

    registration = persist.await_args.args[1]
    assert registration.model_turn_limit == 12
    assert (registration.graph_name, registration.graph_version) == (
        "runtime_graph",
        "v1",
    )
    assert handle.created is False
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_planning_start_uses_dedicated_graph_and_no_agent_turn_limit() -> None:
    tenant_id = uuid.uuid4()
    command = StartRunCommand(
        tenant_id=tenant_id,
        source_type="chat",
        goal="Coordinate the Group",
        run_kind="orchestration",
        system_role="group_planning",
        model_id=uuid.uuid4(),
        idempotency_key="start:planning:1",
        payload={"candidate_agents": []},
        delivery_status="pending",
    )
    run = _run(
        tenant_id=tenant_id,
        agent_id=None,
        model_turn_limit=None,
        run_kind="orchestration",
        system_role="group_planning",
    )
    db = _session()

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(
            return_value=RegisteredRun(run, _stored_command(run, "start"), True)
        ),
    ) as persist:
        await RuntimeCommandIntake(db, settings=_settings(enabled=True)).start_run(
            command
        )

    registration = persist.await_args.args[1]
    assert registration.model_turn_limit is None
    assert (registration.graph_name, registration.graph_version) == (
        "runtime_graph_group_planning",
        "v2",
    )
    assert persist.await_args.kwargs["start_payload"] == {
        "candidate_agents": []
    }
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_new_start_checks_rollout_before_loading_agent_or_persisting() -> None:
    tenant_id = uuid.uuid4()
    db = _session()

    with patch(
        "app.services.agent_runtime.adapter.register_run_with_start",
        new=AsyncMock(),
    ) as persist:
        with pytest.raises(RuntimeAdapterError) as raised:
            await RuntimeCommandIntake(
                db,
                settings=_settings(enabled=False),
            ).start_run(_start(tenant_id, uuid.uuid4()))

    assert raised.value.code == "runtime_v2_disabled"
    assert db.execute.await_count == 0
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_accepts_a_shared_thread_identity_and_cancel_is_scoped_to_run() -> None:
    tenant_id = uuid.uuid4()
    run = _run(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
    )
    resume = _stored_command(run, "resume")
    cancel = _stored_command(run, "cancel")
    db = _session(run, run)

    with (
        patch(
            "app.services.agent_runtime.adapter.enqueue_resume",
            new=AsyncMock(return_value=EnqueuedCommand(resume, True)),
        ),
        patch(
            "app.services.agent_runtime.adapter.enqueue_cancel",
            new=AsyncMock(return_value=EnqueuedCommand(cancel, True)),
        ),
    ):
        intake = RuntimeCommandIntake(db, settings=_settings(enabled=True))
        resumed = await intake.resume_run(
            ResumeRunCommand(
                tenant_id=tenant_id,
                run_id=run.id,
                idempotency_key="resume:1",
                payload={"value": "continue"},
            )
        )
        cancelled = await intake.cancel_run(
            CancelRunCommand(
                tenant_id=tenant_id,
                run_id=run.id,
                idempotency_key="cancel:1",
            )
        )

    assert resumed.thread_id == run.runtime_thread_id
    assert cancelled.run_id == run.id


def test_command_intake_has_no_query_or_stream_facade() -> None:
    intake = RuntimeCommandIntake(_session(), settings=_settings(enabled=True))

    assert not hasattr(runtime_adapter, "TransactionalAgentRuntimeAdapter")
    assert not hasattr(intake, "get_run_state")
    assert not hasattr(intake, "stream_run")


@pytest.mark.asyncio
async def test_callers_cannot_override_reserved_runtime_metadata() -> None:
    tenant_id = uuid.uuid4()
    command = _start(tenant_id, uuid.uuid4())
    command.payload[RUNTIME_COMMAND_METADATA_KEY] = {"forged": True}

    with pytest.raises(RuntimeAdapterError) as raised:
        await RuntimeCommandIntake(
            _session(),
            settings=_settings(enabled=True),
        ).start_run(command)

    assert raised.value.code == "reserved_runtime_metadata"
