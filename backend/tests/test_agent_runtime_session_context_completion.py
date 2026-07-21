"""Terminal SessionContextDelta receipt and optimistic merge tests."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import uuid

import pytest

from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.session_context_completion import (
    SessionContextCompletionError,
    SessionContextCompletionHandler,
)
from app.services.agent_runtime.session_context_service import (
    SessionContextCandidate,
    SessionContextSnapshot,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _Result:
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
    def __init__(self, stored_run: AgentRun) -> None:
        self.stored_run = stored_run
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, statement) -> _Result:
        del statement
        return _Result(self.stored_run)

    async def flush(self) -> None:
        self.flushes += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return self.sessions.popleft()


class _ContextService:
    def __init__(
        self,
        snapshots: list[SessionContextSnapshot],
    ) -> None:
        self.snapshots = deque(snapshots)
        self.compare_calls: list[tuple[int, SessionContextCandidate]] = []

    async def load_snapshot(self, db, *, tenant_id, session_id):
        del db, tenant_id, session_id
        return self.snapshots.popleft()

    async def compare_and_swap(
        self,
        db,
        *,
        tenant_id,
        session_id,
        expected_version,
        expected_covered_through_message_id,
        candidate,
    ):
        del db, tenant_id, session_id, expected_covered_through_message_id
        self.compare_calls.append((expected_version, candidate))
        return replace(
            _snapshot(version=expected_version),
            version=expected_version + 1,
            summary=candidate.summary,
            requirements=tuple(candidate.requirements),
            decisions=tuple(candidate.decisions),
            open_items=tuple(candidate.open_items),
            evidence_refs=tuple(candidate.evidence_refs),
            workspace_refs=tuple(candidate.workspace_refs),
            covered_through_message_id=candidate.covered_through_message_id,
        )


def _snapshot(
    *,
    version: int = 1,
    watermark: uuid.UUID | None = None,
    summary: str = "old",
    requirements: tuple = (),
    decisions: tuple = (),
    open_items: tuple = (),
    evidence_refs: tuple = (),
    workspace_refs: tuple = (),
) -> SessionContextSnapshot:
    return SessionContextSnapshot(
        version=version,
        summary=summary,
        requirements=requirements,
        decisions=decisions,
        open_items=open_items,
        evidence_refs=evidence_refs,
        workspace_refs=workspace_refs,
        covered_through_message_id=watermark,
    )


def _records(
    *,
    direct: bool = False,
) -> tuple[RuntimeRunRecord, CheckpointObservation, AgentRun]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="answer",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(agent_id),
        session_id=str(session_id),
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(session_id if direct else run_id),
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
    state: RuntimeGraphState = {
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
            "session_context_delta": {
                "source_run_id": str(run_id),
                "new_requirements": ["preserve wording"],
                "new_decisions": ["use checkpoint"],
                "resolved_open_items": [],
                "new_open_items": ["ship"],
                "evidence_refs": ["checkpoint://terminal"],
                "workspace_refs": ["workspace://runtime"],
                "result_summary": "answer completed",
            },
        },
    }
    checkpoint = CheckpointObservation("checkpoint-terminal", state)
    stored_run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        source_type="chat",
        goal="answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(session_id if direct else run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )
    return run, checkpoint, stored_run


@pytest.mark.asyncio
async def test_direct_thread_terminal_does_not_run_session_compact() -> None:
    run, checkpoint, _stored_run = _records(direct=True)
    handler = SessionContextCompletionHandler(
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        context_service=_ContextService([]),  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)


@pytest.mark.asyncio
async def test_terminal_delta_and_receipt_commit_together_and_replay_is_noop() -> None:
    run, checkpoint, stored_run = _records()
    message_id = uuid.uuid4()
    snapshot = _snapshot(
        watermark=message_id,
        summary="existing summary",
        requirements=("keep exact wording",),
        decisions=("use checkpoint",),
        open_items=(
            "resolved item",
            "keep item",
            {"id": "structured", "state": "open"},
        ),
        evidence_refs=("evidence://existing",),
        workspace_refs=("workspace://existing",),
    )
    checkpoint.state["lifecycle"]["session_context_delta"] = {
        "source_run_id": str(run.run_id),
        "new_requirements": ["keep exact wording", "new requirement"],
        "new_decisions": ["use checkpoint", "new decision"],
        "resolved_open_items": [
            "resolved item",
            {"state": "open", "id": "structured"},
        ],
        "new_open_items": ["new item"],
        "evidence_refs": ["evidence://existing", "evidence://new"],
        "workspace_refs": ["workspace://existing", "workspace://new"],
        "result_summary": "answer completed",
    }
    context_service = _ContextService([snapshot, snapshot])
    first_load = _Session(stored_run)
    first_commit = _Session(stored_run)
    replay = _Session(stored_run)
    factory = _SessionFactory(first_load, first_commit, replay)
    handler = SessionContextCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
        context_service=context_service,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)
    await handler.handle(run=run, checkpoint=checkpoint)

    assert stored_run.session_context_applied_checkpoint_id == "checkpoint-terminal"
    assert first_commit.flushes == 1
    assert context_service.compare_calls[0][0] == 1
    candidate = context_service.compare_calls[0][1]
    assert candidate.summary == "existing summary\n\nanswer completed"
    assert candidate.requirements == (
        "keep exact wording",
        "new requirement",
    )
    assert candidate.decisions == ("use checkpoint", "new decision")
    assert candidate.open_items == ("keep item", "new item")
    assert candidate.evidence_refs == (
        "evidence://existing",
        "evidence://new",
    )
    assert candidate.workspace_refs == (
        "workspace://existing",
        "workspace://new",
    )
    assert candidate.covered_through_message_id == message_id
    assert factory.calls == 3


@pytest.mark.asyncio
async def test_concurrent_context_change_remerges_from_the_winning_snapshot() -> None:
    run, checkpoint, stored_run = _records()
    message_id = uuid.uuid4()
    old = _snapshot(version=2, summary="old", watermark=message_id)
    winner = _snapshot(
        version=3,
        summary="winner",
        watermark=message_id,
        decisions=("concurrent decision",),
    )
    context_service = _ContextService([old, winner, winner, winner])
    factory = _SessionFactory(*[_Session(stored_run) for _ in range(4)])
    handler = SessionContextCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
        context_service=context_service,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert context_service.compare_calls[0][0] == 3
    assert context_service.compare_calls[0][1].summary == "winner\n\nanswer completed"
    assert context_service.compare_calls[0][1].decisions == (
        "concurrent decision",
        "use checkpoint",
    )
    assert context_service.compare_calls[0][1].covered_through_message_id == message_id
    assert stored_run.session_context_applied_checkpoint_id == "checkpoint-terminal"


@pytest.mark.asyncio
async def test_different_terminal_checkpoint_cannot_replace_existing_receipt() -> None:
    run, checkpoint, stored_run = _records()
    stored_run.session_context_applied_checkpoint_id = "another-checkpoint"
    handler = SessionContextCompletionHandler(
        session_factory=_SessionFactory(_Session(stored_run)),  # type: ignore[arg-type]
        context_service=_ContextService([]),  # type: ignore[arg-type]
    )

    with pytest.raises(SessionContextCompletionError) as exc_info:
        await handler.handle(run=run, checkpoint=checkpoint)

    assert exc_info.value.code == "session_context_receipt_conflict"
