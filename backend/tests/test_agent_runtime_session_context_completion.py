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
    SessionCompactRequest,
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
        messages: tuple[dict, ...],
    ) -> None:
        self.snapshots = deque(snapshots)
        self.messages = messages
        self.compare_calls: list[tuple[int, SessionContextCandidate]] = []

    async def load_snapshot(self, db, *, tenant_id, session_id):
        del db, tenant_id, session_id
        return self.snapshots.popleft()

    async def load_compactable_messages_after_watermark(
        self,
        db,
        *,
        tenant_id,
        session_id,
        covered_through_message_id,
    ):
        del db, tenant_id, session_id, covered_through_message_id
        return self.messages

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
            covered_through_message_id=candidate.covered_through_message_id,
        )


class _Compactor:
    def __init__(self) -> None:
        self.requests: list[SessionCompactRequest] = []

    async def compact(self, request: SessionCompactRequest) -> SessionContextCandidate:
        self.requests.append(request)
        watermark = (
            uuid.UUID(request.messages[-1]["id"])
            if request.messages
            else request.snapshot.covered_through_message_id
        )
        return SessionContextCandidate(
            summary=f"merged-v{request.snapshot.version}",
            requirements=request.delta.new_requirements,
            decisions=request.delta.new_decisions,
            open_items=request.delta.new_open_items,
            evidence_refs=request.delta.evidence_refs,
            workspace_refs=request.delta.workspace_refs,
            covered_through_message_id=watermark,
        )


def _snapshot(
    *,
    version: int = 1,
    watermark: uuid.UUID | None = None,
) -> SessionContextSnapshot:
    return SessionContextSnapshot(
        version=version,
        summary="old",
        requirements=(),
        decisions=(),
        open_items=(),
        evidence_refs=(),
        workspace_refs=(),
        covered_through_message_id=watermark,
    )


def _records() -> tuple[RuntimeRunRecord, CheckpointObservation, AgentRun]:
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
        thread_id=str(run_id),
        runtime_type="langgraph",
        registry=registry,
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
        runtime_thread_id=str(run_id),
        graph_name="runtime_graph",
        graph_version="v1",
        lane_held=False,
        delivery_status="pending",
    )
    return run, checkpoint, stored_run


@pytest.mark.asyncio
async def test_terminal_delta_and_receipt_commit_together_and_replay_is_noop() -> None:
    run, checkpoint, stored_run = _records()
    message_id = uuid.uuid4()
    snapshot = _snapshot()
    context_service = _ContextService(
        [snapshot, snapshot],
        ({"id": str(message_id), "role": "assistant", "content": "done"},),
    )
    compactor = _Compactor()
    first_load = _Session(stored_run)
    first_commit = _Session(stored_run)
    replay = _Session(stored_run)
    factory = _SessionFactory(first_load, first_commit, replay)
    handler = SessionContextCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
        context_service=context_service,  # type: ignore[arg-type]
        compactor=compactor,
    )

    await handler.handle(run=run, checkpoint=checkpoint)
    await handler.handle(run=run, checkpoint=checkpoint)

    assert stored_run.session_context_applied_checkpoint_id == "checkpoint-terminal"
    assert first_commit.flushes == 1
    assert len(compactor.requests) == 1
    assert context_service.compare_calls[0][0] == 1
    assert context_service.compare_calls[0][1].covered_through_message_id == message_id
    assert factory.calls == 3


@pytest.mark.asyncio
async def test_concurrent_context_change_recompacts_from_the_winning_snapshot() -> None:
    run, checkpoint, stored_run = _records()
    message_id = uuid.uuid4()
    old = _snapshot(version=2)
    winner = _snapshot(version=3)
    context_service = _ContextService(
        [old, winner, winner, winner],
        ({"id": str(message_id), "role": "assistant", "content": "done"},),
    )
    compactor = _Compactor()
    factory = _SessionFactory(*[_Session(stored_run) for _ in range(4)])
    handler = SessionContextCompletionHandler(
        session_factory=factory,  # type: ignore[arg-type]
        context_service=context_service,  # type: ignore[arg-type]
        compactor=compactor,
    )

    await handler.handle(run=run, checkpoint=checkpoint)

    assert [request.snapshot.version for request in compactor.requests] == [2, 3]
    assert context_service.compare_calls[0][0] == 3
    assert stored_run.session_context_applied_checkpoint_id == "checkpoint-terminal"


@pytest.mark.asyncio
async def test_different_terminal_checkpoint_cannot_replace_existing_receipt() -> None:
    run, checkpoint, stored_run = _records()
    stored_run.session_context_applied_checkpoint_id = "another-checkpoint"
    handler = SessionContextCompletionHandler(
        session_factory=_SessionFactory(_Session(stored_run)),  # type: ignore[arg-type]
        context_service=_ContextService([], ()),  # type: ignore[arg-type]
        compactor=_Compactor(),
    )

    with pytest.raises(SessionContextCompletionError) as exc_info:
        await handler.handle(run=run, checkpoint=checkpoint)

    assert exc_info.value.code == "session_context_receipt_conflict"
