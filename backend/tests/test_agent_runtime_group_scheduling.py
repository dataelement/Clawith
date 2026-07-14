"""Group acknowledgement and checkpoint-authoritative lane release tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
)
from app.services.agent_runtime.delivery import DeliveryReceipt, DeliveryRequest
from app.services.agent_runtime.group_acknowledgement import (
    RuntimeGroupStartAcknowledgementHandler,
)
from app.services.agent_runtime.scheduling_lane import SchedulingLaneCompletionHandler
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    def __init__(self, session: "_Session") -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.session.transaction_exited = True
        return False


class _Session:
    def __init__(self, *values) -> None:
        self.values = list(values)
        self.flushes = 0
        self.transaction_exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction(self)

    async def execute(self, _statement):
        if not self.values:
            raise AssertionError("unexpected database query")
        return _Result(self.values.pop(0))

    async def flush(self):
        self.flushes += 1


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self):
        return self.session


def _records(*, target_kind: str = "group"):
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    model_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="Respond in the group",
        run_kind="foreground",
        source_type="chat",
        model_id=str(model_id),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
    )
    run_record = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        registry=registry,
    )
    command = RuntimeCommandRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        command_type="start",
        payload={},
        actor_user_id=uuid.uuid4(),
        actor_agent_id=None,
    )
    run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=uuid.UUID(registry.agent_id),
        session_id=uuid.UUID(registry.session_id),
        source_type="chat",
        source_execution_id=f"group_mention:{uuid.uuid4()}:agent:{registry.agent_id}",
        goal=registry.goal,
        run_kind="foreground",
        model_id=model_id,
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime",
        graph_version="v1",
        scheduling_lane_key=f"group_mention:{tenant_id}:{registry.agent_id}",
        scheduling_position_created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        scheduling_position_id=uuid.uuid4(),
        lane_held=True,
        lane_claimed_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        delivery_status="pending",
        delivery_target={"kind": target_kind},
    )
    return run_record, command, run


def _checkpoint(run: RuntimeRunRecord, *, status: str) -> CheckpointObservation:
    state: RuntimeGraphState = {
        "registry": run.registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=1,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": status,  # type: ignore[typeddict-item]
            "next_route": "terminal",
            "last_applied_command_ids": [],
        },
    }
    return CheckpointObservation(checkpoint_id="checkpoint-1", state=state)


@pytest.mark.asyncio
async def test_group_ack_is_an_idempotent_delivery_before_graph_execution() -> None:
    run_record, command, run = _records()
    session = _Session(run.delivery_target)
    handler = RuntimeGroupStartAcknowledgementHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )
    message_id = uuid.uuid4()
    receipt = DeliveryReceipt(
        tenant_id=run.tenant_id,
        run_id=run.id,
        idempotency_key=f"run:{run.id}:ack",
        status="delivered",
        delivery_kind="ack",
        checkpoint_id=None,
        message_id=message_id,
        requested_session_id=None,
        actual_session_id=None,
        fallback_reason=None,
        error_code=None,
    )

    async def publish(**_kwargs):
        assert session.transaction_exited is True

    with (
        patch(
            "app.services.agent_runtime.group_acknowledgement.deliver_runtime_message",
            new=AsyncMock(return_value=receipt),
        ) as deliver,
        patch(
            "app.services.agent_runtime.group_acknowledgement.publish_committed_group_message",
            new=AsyncMock(side_effect=publish),
        ) as broadcast,
    ):
        await handler.handle(run=run_record, command=command, checkpoint=None)

    request = deliver.await_args.args[1]
    assert isinstance(request, DeliveryRequest)
    assert request.run_id == run.id
    assert request.kind == "ack"
    assert request.content == "收到，我开始处理。"
    assert request.idempotency_key == f"run:{run.id}:ack"
    broadcast.assert_awaited_once_with(
        tenant_id=run.tenant_id,
        message_id=message_id,
    )


@pytest.mark.asyncio
async def test_non_group_start_does_not_emit_group_ack() -> None:
    run_record, command, run = _records(target_kind="direct")
    handler = RuntimeGroupStartAcknowledgementHandler(
        session_factory=_SessionFactory(_Session(run.delivery_target)),  # type: ignore[arg-type]
    )

    with patch(
        "app.services.agent_runtime.group_acknowledgement.deliver_runtime_message",
        new=AsyncMock(),
    ) as deliver:
        await handler.handle(run=run_record, command=command, checkpoint=None)

    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_checkpoint_releases_lane_without_reading_projection() -> None:
    run_record, _, run = _records()
    session = _Session(run)
    handler = SchedulingLaneCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    await handler.handle(
        run=run_record,
        checkpoint=_checkpoint(run_record, status="completed"),
    )

    assert run.lane_held is False
    assert run.lane_claimed_at is None
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_non_terminal_checkpoint_keeps_lane_held() -> None:
    run_record, _, run = _records()
    session = _Session(run)
    handler = SchedulingLaneCompletionHandler(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    await handler.handle(
        run=run_record,
        checkpoint=_checkpoint(run_record, status="waiting_user"),
    )

    assert run.lane_held is True
    assert session.flushes == 0
