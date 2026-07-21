"""Strict Group trigger cutoff capture and replay regressions."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import uuid

import pytest

from app.services.agent_runtime.context_builder import ContextBuildError, ContextBuilder
from app.services.agent_runtime.group_context_builder import GroupContextCapture
from app.services.agent_runtime.session_context_service import (
    MessagePosition,
    SessionContextCandidate,
    SessionContextPack,
    SessionContextSnapshot,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    async def execute(self, _statement):
        return _ScalarResult("group")


class _GroupContextBuilder:
    async def capture(
        self,
        _db,
        *,
        initial_input,
        pending_messages,
        recent_messages,
        **_kwargs,
    ) -> GroupContextCapture:
        return GroupContextCapture(
            initial_input=dict(initial_input),
            pending_messages=tuple(dict(message) for message in pending_messages),
            recent_messages=tuple(dict(message) for message in recent_messages),
        )


class _ContextService:
    def __init__(self, *packs: SessionContextPack) -> None:
        self.packs = deque(packs)
        self.calls: list[MessagePosition] = []
        self.write_calls = 0

    async def load_context_pack_through(
        self,
        _db,
        *,
        tenant_id,
        session_id,
        cutoff,
    ) -> SessionContextPack:
        del tenant_id, session_id
        self.calls.append(cutoff)
        if not self.packs:
            raise AssertionError("unexpected cutoff context load")
        return self.packs.popleft()

    async def load_context_pack(self, *_args, **_kwargs):
        raise AssertionError("Group Agent capture must use the cutoff-specific path")

    async def compare_and_swap(self, *_args, **_kwargs):
        self.write_calls += 1
        raise AssertionError("Transient Group cutoff rebuild must not mutate shared state")


class _Compactor:
    def __init__(self, *summaries: str) -> None:
        self.summaries = deque(summaries)
        self.requests = []

    async def compact(self, request):
        self.requests.append(request)
        watermark = (
            uuid.UUID(str(request.messages[-1]["id"]))
            if request.messages
            else request.snapshot.covered_through_message_id
        )
        return SessionContextCandidate(
            summary=self.summaries.popleft(),
            requirements=("bounded",),
            decisions=(),
            open_items=(),
            evidence_refs=(),
            workspace_refs=(),
            covered_through_message_id=watermark,
        )


def _message(
    message_id: uuid.UUID,
    *,
    created_at: datetime,
    content: str,
) -> dict:
    return {
        "id": str(message_id),
        "role": "user",
        "content": content,
        "created_at": created_at.isoformat(),
    }


def _snapshot(
    *,
    version: int,
    summary: str,
    watermark: uuid.UUID | None,
) -> SessionContextSnapshot:
    return SessionContextSnapshot(
        version=version,
        summary=summary,
        requirements=(),
        decisions=(),
        open_items=(),
        evidence_refs=(),
        workspace_refs=(),
        covered_through_message_id=watermark,
    )


def _initial(message_id: uuid.UUID, created_at: datetime) -> dict:
    return {
        "message_id": str(message_id),
        "context_cutoff": {
            "message_id": str(message_id),
            "created_at": created_at.isoformat(),
        },
    }


async def _capture(
    builder: ContextBuilder,
    *,
    message_id: uuid.UUID,
    created_at: datetime,
    initial_input: dict | None = None,
) -> RunInputSnapshots:
    return await builder.capture_run_inputs(
        _Db(),  # type: ignore[arg-type]
        tenant_id=uuid.UUID(int=100),
        session_id=uuid.UUID(int=101),
        agent_id=uuid.UUID(int=102),
        source_type="chat",
        source_id=str(message_id),
        scheduling_position_created_at=created_at,
        scheduling_position_id=message_id,
        initial_input=initial_input or _initial(message_id, created_at),
    )


def _builder(
    service: _ContextService,
    compactor: _Compactor | None = None,
) -> ContextBuilder:
    return ContextBuilder(
        service,  # type: ignore[arg-type]
        group_context_builder=_GroupContextBuilder(),  # type: ignore[arg-type]
        session_context_compactor=compactor,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_latest_compact_after_cutoff_is_transiently_rebuilt_without_mutation() -> None:
    cutoff_id = uuid.UUID(int=20)
    old_id = uuid.UUID(int=10)
    pack = SessionContextPack(
        snapshot=SessionContextSnapshot.empty(),
        pending_messages=(
            _message(old_id, created_at=NOW - timedelta(seconds=1), content="old"),
        ),
        recent_messages=(
            _message(cutoff_id, created_at=NOW, content="trigger"),
        ),
        requires_transient_rebuild=True,
    )
    service = _ContextService(pack)
    compactor = _Compactor("rebuilt only through cutoff")

    snapshots = await _capture(
        _builder(service, compactor),
        message_id=cutoff_id,
        created_at=NOW,
    )

    assert snapshots.session_context["summary"] == "rebuilt only through cutoff"
    assert snapshots.session_context["version"] == 0
    assert snapshots.session_context["covered_through_message_id"] == str(old_id)
    assert snapshots.pending_session_messages == ()
    assert [message["id"] for message in snapshots.recent_session_messages] == [
        str(cutoff_id)
    ]
    assert service.write_calls == 0
    assert len(compactor.requests) == 1
    assert compactor.requests[0].snapshot == SessionContextSnapshot.empty()
    assert [message["id"] for message in compactor.requests[0].messages] == [
        str(old_id)
    ]


@pytest.mark.asyncio
async def test_queued_siblings_with_one_cutoff_freeze_equal_inputs() -> None:
    cutoff_id = uuid.UUID(int=20)
    recent = (
        _message(cutoff_id, created_at=NOW, content="same trigger"),
    )
    selected = _snapshot(version=3, summary="same bounded context", watermark=None)
    service = _ContextService(
        SessionContextPack(snapshot=selected, recent_messages=recent),
        SessionContextPack(snapshot=selected, recent_messages=recent),
    )
    builder = _builder(service)

    first = await _capture(builder, message_id=cutoff_id, created_at=NOW)
    second = await _capture(builder, message_id=cutoff_id, created_at=NOW)

    assert first == second
    assert service.calls == [
        MessagePosition(created_at=NOW, message_id=cutoff_id),
        MessagePosition(created_at=NOW, message_id=cutoff_id),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_input", "source_id", "position_id", "position_created_at"),
    [
        ({"message_id": str(uuid.UUID(int=20))}, str(uuid.UUID(int=20)), uuid.UUID(int=20), NOW),
        (
            _initial(uuid.UUID(int=20), NOW),
            str(uuid.UUID(int=21)),
            uuid.UUID(int=20),
            NOW,
        ),
        (
            _initial(uuid.UUID(int=20), NOW),
            str(uuid.UUID(int=20)),
            uuid.UUID(int=21),
            NOW,
        ),
        (
            _initial(uuid.UUID(int=20), NOW),
            str(uuid.UUID(int=20)),
            uuid.UUID(int=20),
            NOW + timedelta(seconds=1),
        ),
    ],
)
async def test_missing_or_mismatched_group_cutoff_fails_before_context_read(
    initial_input,
    source_id,
    position_id,
    position_created_at,
) -> None:
    service = _ContextService()
    builder = _builder(service)

    with pytest.raises(ContextBuildError) as exc_info:
        await builder.capture_run_inputs(
            _Db(),  # type: ignore[arg-type]
            tenant_id=uuid.UUID(int=100),
            session_id=uuid.UUID(int=101),
            agent_id=uuid.UUID(int=102),
            source_type="chat",
            source_id=source_id,
            scheduling_position_created_at=position_created_at,
            scheduling_position_id=position_id,
            initial_input=initial_input,
        )

    assert exc_info.value.code == "invalid_group_context_cutoff"
    assert service.calls == []


@pytest.mark.asyncio
async def test_later_group_run_uses_its_own_later_cutoff_data() -> None:
    first_id = uuid.UUID(int=20)
    later_id = uuid.UUID(int=30)
    service = _ContextService(
        SessionContextPack(
            snapshot=SessionContextSnapshot.empty(),
            recent_messages=(
                _message(first_id, created_at=NOW, content="first"),
            ),
        ),
        SessionContextPack(
            snapshot=SessionContextSnapshot.empty(),
            recent_messages=(
                _message(first_id, created_at=NOW, content="first"),
                _message(
                    later_id,
                    created_at=NOW + timedelta(seconds=1),
                    content="later",
                ),
            ),
        ),
    )
    builder = _builder(service)

    first = await _capture(builder, message_id=first_id, created_at=NOW)
    later = await _capture(
        builder,
        message_id=later_id,
        created_at=NOW + timedelta(seconds=1),
    )

    assert [message["content"] for message in first.recent_session_messages] == [
        "first"
    ]
    assert [message["content"] for message in later.recent_session_messages] == [
        "first",
        "later",
    ]


def _runtime_state(snapshots: RunInputSnapshots) -> RuntimeGraphState:
    registry = RunRegistrySnapshot(
        tenant_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        goal="bounded group task",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
    )
    return {
        "registry": registry,
        "snapshots": snapshots,
        "messages": [],
        "lifecycle": {"status": "running", "next_route": "model"},
    }


def _runtime_context(state: RuntimeGraphState) -> RuntimeContext:
    registry = state["registry"]
    return RuntimeContext(
        tenant_id=registry.tenant_id,
        run_id=registry.run_id,
        command_id=str(uuid.uuid4()),
        executor=object(),  # type: ignore[arg-type]
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
    )


@pytest.mark.asyncio
async def test_group_checkpoint_replay_never_refreshes_cutoff_snapshot() -> None:
    cutoff_id = uuid.UUID(int=20)
    service = _ContextService(
        SessionContextPack(
            snapshot=SessionContextSnapshot.empty(),
            recent_messages=(
                _message(cutoff_id, created_at=NOW, content="frozen"),
            ),
        )
    )
    builder = _builder(service)
    snapshots = await _capture(builder, message_id=cutoff_id, created_at=NOW)
    state = _runtime_state(snapshots)

    first = await builder.build(state, _runtime_context(state))
    second = await builder.build(
        state,
        _runtime_context(state),
        resume_input={"content": "resume"},
    )

    assert first.session_context_snapshot == second.session_context_snapshot
    assert first.recent_session_messages_snapshot == second.recent_session_messages_snapshot
    assert [message["content"] for message in second.recent_session_messages_snapshot] == [
        "frozen"
    ]
    assert len(service.calls) == 1
