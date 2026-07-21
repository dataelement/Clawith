"""TriggerExecution entrypoint cutover tests for the durable Runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.trigger import AgentTrigger
from app.models.trigger_execution import TriggerExecution
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.trigger_runtime.intake import (
    TriggerRuntimeIntakeError,
    build_trigger_context,
    enqueue_trigger_runtime,
)


class _Session:
    pass


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        AGENT_RUNTIME_V2_ENABLED=enabled,
        AGENT_RUNTIME_V2_SOURCE_TYPES="trigger" if enabled else "",
    )


def _records() -> tuple[TriggerExecution, AgentTrigger, Agent]:
    agent_id = uuid.uuid4()
    trigger = AgentTrigger(
        id=uuid.uuid4(),
        agent_id=agent_id,
        name="daily-check",
        type="webhook",
        config={
            "_webhook_payload": "{\"status\": \"ready\"}",
            "_origin_user_id": str(uuid.uuid4()),
            "_origin_source_channel": "web",
        },
        reason="Check the upstream status",
        is_enabled=True,
        fire_count=0,
    )
    execution = TriggerExecution(
        id=uuid.uuid4(),
        trigger_id=trigger.id,
        agent_id=agent_id,
        source="webhook",
        status="pending",
        idempotency_key="delivery-1",
        payload={},
        payload_text="{\"status\": \"ready\"}",
    )
    agent = Agent(
        id=agent_id,
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        name="Watcher",
        role_description="Watch upstream systems",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
    )
    return execution, trigger, agent


@pytest.mark.asyncio
async def test_runtime_trigger_pins_execution_identity_and_caller_transaction() -> None:
    execution, trigger, agent = _records()
    session = _Session()
    reflection_session = SimpleNamespace(id=uuid.uuid4())
    target = {
        "kind": "primary_user_session",
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
    }
    handle = RunHandle(
        tenant_id=agent.tenant_id,
        run_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with (
        patch(
            "app.services.trigger_runtime.intake._ensure_trigger_session",
            new=AsyncMock(return_value=reflection_session),
        ),
        patch(
            "app.services.trigger_runtime.intake._resolve_trigger_delivery_target",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.services.trigger_runtime.intake.RuntimeCommandIntake.start_run",
            new=AsyncMock(return_value=handle),
        ) as start_run,
    ):
        result = await enqueue_trigger_runtime(
            session,  # type: ignore[arg-type]
            execution=execution,
            trigger=trigger,
            agent=agent,
            settings_override=_settings(enabled=True),
        )

    assert result == handle
    command = start_run.await_args.args[0]
    assert isinstance(command, StartRunCommand)
    assert command.tenant_id == agent.tenant_id
    assert command.agent_id == agent.id
    assert command.session_id == reflection_session.id
    assert command.source_type == "trigger"
    assert command.source_id == str(trigger.id)
    assert command.source_execution_id == str(execution.id)
    assert command.idempotency_key == f"start:trigger:{execution.id}"
    assert command.model_id == agent.primary_model_id
    assert command.delivery_status == "pending"
    assert command.delivery_target == target
    assert command.payload["trigger_execution_id"] == str(execution.id)
    assert command.payload["message_id"] == str(
        uuid.uuid5(execution.id, "runtime-trigger-input")
    )
    assert command.payload["input_content"].startswith(
        "===== 本次唤醒上下文 ====="
    )
    assert "trigger_context" not in command.payload
    assert command.payload["trigger_event_data"] == {
        "webhook_payload": '{"status": "ready"}'
    }
    assert execution.status == "processing"
    assert execution.started_at is not None
    assert execution.lease_owner is None


@pytest.mark.asyncio
async def test_disabled_trigger_rollout_leaves_occurrence_for_legacy_claim() -> None:
    execution, trigger, agent = _records()

    with patch(
        "app.services.trigger_runtime.intake.RuntimeCommandIntake.start_run",
        new=AsyncMock(),
    ) as start_run:
        result = await enqueue_trigger_runtime(
            _Session(),  # type: ignore[arg-type]
            execution=execution,
            trigger=trigger,
            agent=agent,
            settings_override=_settings(enabled=False),
        )

    assert result is None
    assert execution.status == "pending"
    start_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_trigger_rejects_cross_agent_execution() -> None:
    execution, trigger, agent = _records()
    execution.agent_id = uuid.uuid4()

    with pytest.raises(TriggerRuntimeIntakeError) as raised:
        await enqueue_trigger_runtime(
            _Session(),  # type: ignore[arg-type]
            execution=execution,
            trigger=trigger,
            agent=agent,
            settings_override=_settings(enabled=True),
        )

    assert raised.value.code == "trigger_execution_scope_mismatch"


def test_trigger_context_keeps_instruction_body_separate_from_event_data() -> None:
    execution, trigger, _ = _records()
    del execution
    context = build_trigger_context([trigger])

    assert "唤醒来源：trigger（触发器触发）" in context
    assert "触发器：daily-check (webhook)" in context
    assert "Check the upstream status" in context
    assert "Webhook Payload" not in context
    assert '{"status": "ready"}' not in context
