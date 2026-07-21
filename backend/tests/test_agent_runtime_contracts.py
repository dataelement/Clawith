from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
import uuid

import pytest

from app.services.agent_runtime.contracts import (
    CancelRunCommand,
    RunHandle,
    RunView,
    RuntimeEvent,
    ResumeRunCommand,
    StartRunCommand,
)


def test_execution_commands_cannot_carry_product_projection_fields() -> None:
    command_types = (StartRunCommand, ResumeRunCommand, CancelRunCommand)

    for command_type in command_types:
        assert not {
            field.name
            for field in fields(command_type)
            if field.name.startswith("projected_") or field.name.startswith("projection_")
        }


def test_start_command_and_handle_are_immutable_runtime_inputs() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    command = StartRunCommand(
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="task",
        goal="Summarize the task artifact",
        run_kind="background",
        idempotency_key="task-execution:1",
        payload={"task_id": str(uuid.uuid4())},
    )
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=uuid.uuid4(),
        thread_id="thread-1",
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )

    with pytest.raises(FrozenInstanceError):
        command.goal = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        handle.thread_id = "changed"  # type: ignore[misc]


def test_run_view_and_runtime_event_are_query_only_values() -> None:
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    view = RunView(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id="thread-1",
        session_id=uuid.uuid4(),
        source_type="chat",
        run_kind="foreground",
        goal="Answer the user",
        runtime_type="langgraph",
        execution_status="running",
        current_node="model",
        model_step_count=1,
        waiting_type=None,
        waiting_reason=None,
        waiting_correlation_id=None,
        result_summary=None,
        error_code=None,
        last_error=None,
        verification_result=None,
        delivery_status="pending",
        applied_checkpoint_id="checkpoint-1",
        checkpoint_created_at=now,
        created_at=now,
        updated_at=now,
    )
    event = RuntimeEvent(
        tenant_id=tenant_id,
        run_id=run_id,
        event_id=uuid.uuid4(),
        event_type="status_changed",
        payload={"status": "running"},
        checkpoint_id="checkpoint-1",
        created_at=now,
    )

    assert view.execution_status == "running"
    assert event.payload == {"status": "running"}
