"""Heartbeat entrypoint cutover tests for the durable Runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.services import heartbeat as heartbeat_service
from app.config import Settings
from app.models.agent import Agent
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.heartbeat_runtime import (
    HeartbeatRuntimeIntakeError,
    enqueue_heartbeat_runtime,
    enqueue_oneshot_runtime,
    enqueue_schedule_runtime,
    heartbeat_source_execution_id,
    schedule_occurrence_id,
)


class _Session:
    pass


def test_heartbeat_entrypoint_has_no_independent_model_tool_loop() -> None:
    assert not hasattr(heartbeat_service, "_execute_heartbeat")


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=enabled,
        AGENT_RUNTIME_V2_SOURCE_TYPES="heartbeat" if enabled else "",
    )


def _agent() -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        name="Heartbeat Agent",
        role_description="Observe and assist",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
    )


@pytest.mark.asyncio
async def test_runtime_heartbeat_pins_claimed_occurrence_and_caller_transaction() -> None:
    agent = _agent()
    occurrence = datetime(2026, 7, 13, 18, 45, 12, 123456, tzinfo=UTC)
    handle = RunHandle(
        tenant_id=agent.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with patch(
        "app.services.heartbeat_runtime.RuntimeCommandIntake.start_run",
        new=AsyncMock(return_value=handle),
    ) as start_run:
        result = await enqueue_heartbeat_runtime(
            _Session(),  # type: ignore[arg-type]
            agent=agent,
            occurrence_at=occurrence,
            instruction="  Review the inbox  ",
            context={
                "recent_activity": [
                    {"action_type": "chat_reply", "summary": "Answered Ray"}
                ]
            },
            settings_override=_settings(enabled=True),
        )

    assert result == handle
    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.tenant_id == agent.tenant_id
    assert command.agent_id == agent.id
    assert command.session_id is None
    assert command.source_type == "heartbeat"
    assert command.source_id == str(agent.id)
    assert command.source_execution_id == (
        f"heartbeat:{agent.id}:2026-07-13T18:45:12.123456Z"
    )
    assert command.goal == "Review the inbox"
    assert command.run_kind == "background"
    assert command.model_id == agent.primary_model_id
    assert command.delivery_status == "not_required"
    assert command.idempotency_key == f"start:{command.source_execution_id}"
    assert "heartbeat_instruction" not in command.payload
    assert command.payload["heartbeat_context"] == {
        "recent_activity": [
            {"action_type": "chat_reply", "summary": "Answered Ray"}
        ]
    }


def test_default_heartbeat_prompt_does_not_advertise_hardcoded_tools() -> None:
    prompt = "\n".join(
        (
            heartbeat_service.DEFAULT_HEARTBEAT_INSTRUCTION,
            heartbeat_service.PRIVATE_AGENT_HEARTBEAT_APPEND,
            heartbeat_service.CUSTOM_HEARTBEAT_GUARDRAILS,
        )
    )

    for hardcoded_tool in (
        "web_search",
        "write_file",
        "plaza_get_new_posts",
        "plaza_create_post",
        "plaza_add_comment",
    ):
        assert hardcoded_tool not in prompt


@pytest.mark.asyncio
async def test_heartbeat_intake_error_does_not_read_expired_agent_identity() -> None:
    class _ExpiringAgent:
        def __init__(self) -> None:
            self._id = uuid.uuid4()
            self._name = "Heartbeat Agent"
            self.expired = False
            self.name_reads = 0
            self.tenant_id = None
            self.is_expired = False
            self.expires_at = None
            self.heartbeat_active_hours = "00:00-23:59"
            self.heartbeat_interval_minutes = 1
            self.last_heartbeat_at = None

        @property
        def id(self):
            return self._id

        @property
        def name(self):
            self.name_reads += 1
            if self.expired:
                raise RuntimeError("expired ORM attribute was accessed")
            return self._name

    agent = _ExpiringAgent()

    class _Result:
        rowcount = 1

        def scalars(self):
            return self

        def all(self):
            return [agent]

    class _NestedTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            if exc_type is not None:
                agent.expired = True
            return False

    class _HeartbeatSession:
        async def execute(self, _statement):
            return _Result()

        def begin_nested(self):
            return _NestedTransaction()

        async def commit(self):
            return None

    @asynccontextmanager
    async def fake_session():
        yield _HeartbeatSession()

    async def fail_intake(*_args, **_kwargs):
        raise HeartbeatRuntimeIntakeError(
            "model_unavailable",
            "Heartbeat Agent has no primary model",
        )

    audit = AsyncMock()
    with (
        patch("app.database.async_session", new=fake_session),
        patch(
            "app.services.agent_runtime.config.decide_runtime_v2",
            return_value=SimpleNamespace(use_v2=True, reason="enabled"),
        ),
        patch(
            "app.services.timezone_utils.get_agent_timezone_sync",
            return_value="UTC",
        ),
        patch(
            "app.services.heartbeat._build_heartbeat_instruction",
            new=AsyncMock(return_value=("Review", {})),
        ),
        patch(
            "app.services.heartbeat_runtime.enqueue_heartbeat_runtime",
            new=fail_intake,
        ),
        patch("app.services.audit_logger.write_audit_log", new=audit),
    ):
        await heartbeat_service._heartbeat_tick()

    assert agent.expired is True
    assert agent.name_reads == 1
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_heartbeat_rollout_leaves_claim_for_legacy_execution() -> None:
    agent = _agent()

    with patch(
        "app.services.heartbeat_runtime.RuntimeCommandIntake.start_run",
        new=AsyncMock(),
    ) as start_run:
        result = await enqueue_heartbeat_runtime(
            _Session(),  # type: ignore[arg-type]
            agent=agent,
            occurrence_at=datetime.now(UTC),
            instruction="Review the inbox",
            settings_override=_settings(enabled=False),
        )

    assert result is None
    start_run.assert_not_awaited()


def test_heartbeat_occurrence_identity_is_stable_across_timezone_offsets() -> None:
    agent_id = uuid.uuid4()
    utc_occurrence = datetime(2026, 7, 13, 18, 45, tzinfo=UTC)
    offset_occurrence = utc_occurrence.astimezone(
        datetime.now().astimezone().tzinfo
    )

    assert heartbeat_source_execution_id(
        agent_id,
        offset_occurrence,
    ) == heartbeat_source_execution_id(agent_id, utc_occurrence)


def test_heartbeat_occurrence_rejects_naive_timestamp() -> None:
    with pytest.raises(HeartbeatRuntimeIntakeError) as raised:
        heartbeat_source_execution_id(
            uuid.uuid4(),
            datetime(2026, 7, 13, 18, 45),
        )

    assert raised.value.code == "invalid_heartbeat_occurrence"


def test_schedule_occurrence_identity_is_stable_across_timezone_views() -> None:
    schedule_id = uuid.uuid4()
    utc_occurrence = datetime(2026, 7, 14, 3, 30, tzinfo=UTC)
    local_occurrence = utc_occurrence.astimezone(timezone(timedelta(hours=8)))

    assert schedule_occurrence_id(
        schedule_id,
        utc_occurrence,
    ) == schedule_occurrence_id(schedule_id, local_occurrence)


@pytest.mark.asyncio
async def test_oneshot_registration_uses_a_unique_background_occurrence() -> None:
    agent = _agent()
    occurrence_id = uuid.uuid4()
    user_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=agent.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with patch(
        "app.services.heartbeat_runtime.RuntimeCommandIntake.start_run",
        new=AsyncMock(return_value=handle),
    ) as start_run:
        result = await enqueue_oneshot_runtime(
            _Session(),  # type: ignore[arg-type]
            agent=agent,
            prompt="  Prepare the OKR report  ",
            occurrence_id=occurrence_id,
            triggered_by_user_id=user_id,
            requested_model_turn_limit=40,
            settings_override=_settings(enabled=True),
        )

    assert result == handle
    command = start_run.await_args.args[0]
    assert command.source_type == "heartbeat"
    assert command.source_id == str(agent.id)
    assert command.source_execution_id == f"oneshot:{agent.id}:{occurrence_id}"
    assert command.goal == "Prepare the OKR report"
    assert command.payload["background_mode"] == "oneshot"
    assert command.payload["triggered_by_user_id"] == str(user_id)
    assert command.requested_model_turn_limit == 40
    assert "requested_max_steps" not in command.payload


@pytest.mark.asyncio
async def test_schedule_registration_pins_the_schedule_occurrence() -> None:
    agent = _agent()
    schedule_id = uuid.uuid4()
    occurrence_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=agent.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with patch(
        "app.services.heartbeat_runtime.RuntimeCommandIntake.start_run",
        new=AsyncMock(return_value=handle),
    ) as start_run:
        result = await enqueue_schedule_runtime(
            _Session(),  # type: ignore[arg-type]
            agent=agent,
            schedule_id=schedule_id,
            occurrence_id=occurrence_id,
            instruction="  Review the weekly pipeline  ",
            settings_override=_settings(enabled=True),
        )

    assert result == handle
    command = start_run.await_args.args[0]
    assert command.source_type == "heartbeat"
    assert command.source_id == str(schedule_id)
    assert command.source_execution_id == f"schedule:{schedule_id}:{occurrence_id}"
    assert command.goal == "[自动调度任务] Review the weekly pipeline"
    assert command.payload["background_mode"] == "schedule"
