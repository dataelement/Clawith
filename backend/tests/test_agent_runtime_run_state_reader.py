"""Typed RunView queries must use the target Command checkpoint exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

from langgraph.types import StateSnapshot
import pytest

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.graph import AgentRuntimeGraph, RuntimeGraphIdentity
from app.services.agent_runtime.langgraph_driver import RuntimeGraphRegistry
from app.services.agent_runtime.run_state_reader import RunStateReadError, RunStateReader
from app.services.agent_runtime.state import RunInputSnapshots, RunRegistrySnapshot


class _Scalars:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalars(self) -> _Scalars:
        return _Scalars(list(self.value))  # type: ignore[arg-type]


class _Session:
    def __init__(self, run: AgentRun, commands: list[AgentRunCommand]) -> None:
        self.results = [_Result(run), _Result(commands)]

    async def execute(self, _statement) -> _Result:
        return self.results.pop(0)


class _Compiled:
    def __init__(self, snapshots: dict[str, StateSnapshot]) -> None:
        self.snapshots = snapshots
        self.state_configs: list[dict] = []
        self.history_filters: list[dict] = []

    async def aget_state(self, config):
        self.state_configs.append(config)
        return self.snapshots[config["configurable"]["checkpoint_id"]]

    async def aget_state_history(self, config, *, filter=None, before=None, limit=None):
        del config, before, limit
        self.history_filters.append(filter)
        for snapshot in self.snapshots.values():
            if all(snapshot.metadata.get(key) == value for key, value in (filter or {}).items()):
                yield snapshot
                return


def _records() -> tuple[AgentRun, AgentRunCommand, RunRegistrySnapshot]:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    run_id = uuid.uuid4()
    run = AgentRun(
        id=run_id,
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id="shared-session-thread",
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="delivered",
        created_at=now,
        updated_at=now,
    )
    command = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="start",
        payload={},
        idempotency_key="start:1",
        status="applied",
        applied_checkpoint_id="checkpoint-target",
        attempt_count=1,
        created_at=now,
        applied_at=now,
    )
    registry = RunRegistrySnapshot(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        goal=run.goal,
        run_kind=run.run_kind,
        source_type=run.source_type,
        model_id=str(run.model_id),
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        agent_id=str(run.agent_id),
        session_id=str(run.session_id),
    )
    return run, command, registry


def _snapshot(
    run: AgentRun,
    command: AgentRunCommand,
    registry: RunRegistrySnapshot,
    *,
    checkpoint_id: str,
    command_id: uuid.UUID | None = None,
) -> StateSnapshot:
    return StateSnapshot(
        values={
            "registry": registry,
            "snapshots": RunInputSnapshots(
                session_context={},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": "completed",
                "next_route": "terminal",
                "model_step_count": 3,
                "result_summary": {"answer": "done"},
                "verification_result": {"outcome": "pass"},
            },
        },
        next=(),
        config={
            "configurable": {
                "thread_id": run.runtime_thread_id,
                "checkpoint_id": checkpoint_id,
            }
        },
        metadata={
            "clawith_run_id": str(run.id),
            "clawith_command_id": str(command_id or command.id),
        },
        created_at="2026-07-16T12:00:01+00:00",
        parent_config=None,
        tasks=(),
        interrupts=(),
    )


def _registry(compiled: _Compiled) -> RuntimeGraphRegistry:
    graph = AgentRuntimeGraph(
        identity=RuntimeGraphIdentity(name="runtime_graph", version="v1"),
        compiled=compiled,  # type: ignore[arg-type]
    )
    return RuntimeGraphRegistry([graph])


def _as_waiting(snapshot: StateSnapshot) -> StateSnapshot:
    snapshot.values["lifecycle"] = {
        "status": "waiting_user",
        "next_route": "waiting",
        "model_step_count": 2,
        "waiting_request": {
            "waiting_type": "user",
            "correlation_id": "confirm-1",
            "question": "Continue?",
        },
    }
    return StateSnapshot(
        values=snapshot.values,
        next=("wait",),
        config=snapshot.config,
        metadata=snapshot.metadata,
        created_at=snapshot.created_at,
        parent_config=snapshot.parent_config,
        tasks=(SimpleNamespace(name="wait", error=None),),
        interrupts=(object(),),
    )


@pytest.mark.asyncio
async def test_applied_run_reads_its_exact_checkpoint_not_thread_latest() -> None:
    run, command, registry = _records()
    target = _snapshot(
        run,
        command,
        registry,
        checkpoint_id="checkpoint-target",
    )
    unrelated = _snapshot(
        run,
        command,
        registry,
        checkpoint_id="checkpoint-newer",
        command_id=uuid.uuid4(),
    )
    compiled = _Compiled({"checkpoint-target": target, "checkpoint-newer": unrelated})

    view = await RunStateReader(
        _Session(run, [command]),  # type: ignore[arg-type]
        graph_registry=_registry(compiled),
    ).get_run_state(run.tenant_id, run.id)

    assert view.execution_status == "completed"
    assert view.applied_checkpoint_id == "checkpoint-target"
    assert view.thread_id == "shared-session-thread"
    assert view.model_step_count == 3
    assert view.verification_result == {"outcome": "pass"}
    assert compiled.state_configs == [
        {
            "configurable": {
                "thread_id": "shared-session-thread",
                "checkpoint_id": "checkpoint-target",
            }
        }
    ]
    assert compiled.history_filters == []


@pytest.mark.asyncio
async def test_applied_cancel_without_checkpoint_is_authoritative() -> None:
    run, command, _ = _records()
    command.command_type = "cancel"
    command.applied_checkpoint_id = None
    compiled = _Compiled({})

    view = await RunStateReader(
        _Session(run, [command]),  # type: ignore[arg-type]
        graph_registry=_registry(compiled),
    ).get_run_state(run.tenant_id, run.id)

    assert view.execution_status == "cancelled"
    assert view.applied_checkpoint_id is None
    assert compiled.state_configs == []


@pytest.mark.asyncio
async def test_exact_checkpoint_with_wrong_command_metadata_fails_closed() -> None:
    run, command, registry = _records()
    wrong = _snapshot(
        run,
        command,
        registry,
        checkpoint_id="checkpoint-target",
        command_id=uuid.uuid4(),
    )

    with pytest.raises(RunStateReadError) as raised:
        await RunStateReader(
            _Session(run, [command]),  # type: ignore[arg-type]
            graph_registry=_registry(_Compiled({"checkpoint-target": wrong})),
        ).get_run_state(run.tenant_id, run.id)

    assert raised.value.code == "checkpoint_command_mismatch"


@pytest.mark.asyncio
async def test_claimed_resume_checkpoint_takes_precedence_over_prior_applied_wait() -> None:
    run, start, registry = _records()
    start.applied_checkpoint_id = "checkpoint-waiting"
    waiting = _as_waiting(
        _snapshot(
            run,
            start,
            registry,
            checkpoint_id="checkpoint-waiting",
        )
    )
    resume = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="resume",
        payload={
            "resume_type": "user_input",
            "correlation_id": "confirm-1",
            "payload": {"content": "yes"},
        },
        idempotency_key="resume:1",
        status="claimed",
        attempt_count=1,
        created_at=start.created_at.replace(microsecond=1),
    )
    resumed = _snapshot(
        run,
        resume,
        registry,
        checkpoint_id="checkpoint-resumed",
    )
    compiled = _Compiled(
        {
            "checkpoint-waiting": waiting,
            "checkpoint-resumed": resumed,
        }
    )

    view = await RunStateReader(
        _Session(run, [start, resume]),  # type: ignore[arg-type]
        graph_registry=_registry(compiled),
    ).get_run_state(run.tenant_id, run.id)

    assert view.execution_status == "completed"
    assert view.applied_checkpoint_id == "checkpoint-resumed"
    assert compiled.history_filters == [
        {
            "clawith_run_id": str(run.id),
            "clawith_command_id": str(resume.id),
        }
    ]


@pytest.mark.asyncio
async def test_pending_resume_without_checkpoint_keeps_prior_applied_wait_visible() -> None:
    run, start, registry = _records()
    start.applied_checkpoint_id = "checkpoint-waiting"
    waiting = _as_waiting(
        _snapshot(
            run,
            start,
            registry,
            checkpoint_id="checkpoint-waiting",
        )
    )
    resume = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="resume",
        payload={
            "resume_type": "user_input",
            "correlation_id": "confirm-1",
            "payload": {"content": "yes"},
        },
        idempotency_key="resume:1",
        status="pending",
        attempt_count=0,
        created_at=start.created_at.replace(microsecond=1),
    )
    compiled = _Compiled({"checkpoint-waiting": waiting})

    view = await RunStateReader(
        _Session(run, [start, resume]),  # type: ignore[arg-type]
        graph_registry=_registry(compiled),
    ).get_run_state(run.tenant_id, run.id)

    assert view.execution_status == "waiting_user"
    assert view.waiting_correlation_id == "confirm-1"
    assert view.applied_checkpoint_id == "checkpoint-waiting"


@pytest.mark.asyncio
async def test_rejected_start_without_checkpoint_is_a_failed_control_boundary() -> None:
    run, start, registry = _records()
    start.status = "rejected"
    start.applied_checkpoint_id = None
    start.error_code = "reconciliation_required"
    compiled = _Compiled({})

    view = await RunStateReader(
        _Session(run, [start]),  # type: ignore[arg-type]
        graph_registry=_registry(compiled),
    ).get_run_state(run.tenant_id, run.id)

    assert view.execution_status == "failed"
    assert view.error_code == "reconciliation_required"
    assert view.applied_checkpoint_id is None
