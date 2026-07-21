"""Heartbeat terminal checkpoint activity projection tests."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid

import pytest

from app.models.activity_log import AgentActivityLog
from app.models.agent_run import AgentRun
from app.models.notification import Notification
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.heartbeat_completion import (
    HeartbeatRuntimeCompletionError,
    HeartbeatRuntimeCompletionHandler,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.results.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return self.sessions.popleft()


def _records(
    *,
    source_type: str = "heartbeat",
    status: str = "completed",
    answer: str | None = "Reviewed two notifications",
    mode: str = "heartbeat",
) -> tuple[RuntimeRunRecord, CheckpointObservation, AgentRun]:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="review the environment",
        run_kind="background",
        source_type=source_type,
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(agent_id),
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
    )
    initial_input: dict = {"background_mode": mode}
    if mode == "schedule":
        schedule_id = uuid.uuid4()
        initial_input.update(
            {
                "schedule_id": str(schedule_id),
                "schedule_instruction": "Review the weekly pipeline",
            }
        )
        source_id = str(schedule_id)
        source_execution_id = f"schedule:{schedule_id}:{uuid.uuid4()}"
    elif mode == "oneshot":
        initial_input.update(
            {
                "triggered_by_user_id": str(uuid.uuid4()),
                "agent_name": "OKR Agent",
            }
        )
        source_id = str(agent_id)
        source_execution_id = f"oneshot:{agent_id}:{uuid.uuid4()}"
    else:
        source_id = str(agent_id)
        source_execution_id = (
            f"heartbeat:{agent_id}:2026-07-13T18:45:00.000000Z"
        )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input=initial_input,
        ),
        "lifecycle": {
            "status": status,
            "next_route": "terminal",
            "final_answer": answer,
            "error": (
                {"code": "model_call_failed"} if status == "failed" else None
            ),
        },  # type: ignore[typeddict-item]
    }
    checkpoint = CheckpointObservation(
        checkpoint_id="checkpoint-terminal",
        state=state,
    )
    stored_run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="heartbeat",
        source_id=source_id,
        source_execution_id=source_execution_id,
        goal="review the environment",
        run_kind="background",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="not_required",
    )
    return run, checkpoint, stored_run


@pytest.mark.asyncio
async def test_useful_heartbeat_result_creates_one_deterministic_activity() -> None:
    run, checkpoint, stored_run = _records()
    db = _Session(stored_run, None)
    created_at = datetime(2026, 7, 13, 19, 0, tzinfo=UTC)
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
        clock=lambda: created_at,
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert db.flushes == 1
    assert len(db.added) == 1
    activity = db.added[0]
    assert isinstance(activity, AgentActivityLog)
    assert activity.id == uuid.uuid5(
        run.run_id,
        "heartbeat-terminal:checkpoint-terminal",
    )
    assert activity.agent_id == stored_run.agent_id
    assert activity.action_type == "heartbeat"
    assert activity.summary == "Heartbeat: Reviewed two notifications"
    assert activity.related_id == run.run_id
    assert activity.created_at == created_at


@pytest.mark.asyncio
async def test_existing_activity_receipt_makes_reconciliation_idempotent() -> None:
    run, checkpoint, stored_run = _records()
    receipt_id = uuid.uuid5(
        run.run_id,
        "heartbeat-terminal:checkpoint-terminal",
    )
    db = _Session(stored_run, receipt_id)
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert db.added == []
    assert db.flushes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "answer"),
    [
        ("completed", "HEARTBEAT OK"),
        ("failed", None),
        ("cancelled", None),
    ],
)
async def test_noop_heartbeat_result_does_not_open_a_session(
    status: str,
    answer: str | None,
) -> None:
    run, checkpoint, _ = _records(status=status, answer=answer)
    factory = _SessionFactory()
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert factory.calls == 0


@pytest.mark.asyncio
async def test_completed_heartbeat_rejects_mismatched_source_identity() -> None:
    run, checkpoint, stored_run = _records()
    stored_run.source_id = str(uuid.uuid4())
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=_SessionFactory(_Session(stored_run)),  # type: ignore[arg-type]
    )

    with pytest.raises(HeartbeatRuntimeCompletionError) as raised:
        await handler.handle(run=run, checkpoint=checkpoint)

    assert raised.value.code == "heartbeat_source_mismatch"


@pytest.mark.asyncio
async def test_schedule_result_creates_schedule_activity_from_checkpoint_input() -> None:
    run, checkpoint, stored_run = _records(mode="schedule")
    db = _Session(stored_run, None)
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    activity = db.added[0]
    assert isinstance(activity, AgentActivityLog)
    assert activity.id == uuid.uuid5(
        run.run_id,
        "schedule-terminal:checkpoint-terminal",
    )
    assert activity.action_type == "schedule_run"
    assert activity.summary == "定时任务执行: Review the weekly pipeline"
    assert activity.related_id == uuid.UUID(stored_run.source_id)


@pytest.mark.asyncio
async def test_failed_oneshot_notifies_the_triggering_user_exactly_once() -> None:
    run, checkpoint, stored_run = _records(
        mode="oneshot",
        status="failed",
        answer=None,
    )
    db = _Session(stored_run, None)
    handler = HeartbeatRuntimeCompletionHandler(
        session_factory=_SessionFactory(db),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    notification = db.added[0]
    assert isinstance(notification, Notification)
    assert notification.id == uuid.uuid5(
        run.run_id,
        "oneshot-terminal:checkpoint-terminal",
    )
    assert notification.user_id == uuid.UUID(
        checkpoint.state["snapshots"].initial_input["triggered_by_user_id"]
    )
    assert notification.title == "OKR Agent task failed"
    assert notification.body == "任务执行未完成（model_call_failed）"
