"""Focused Session Context watermark, recent-window, and CAS tests."""

from collections import deque
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.session_context_state import SessionContextState
from app.services.agent_runtime import session_context_service as service


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, *results):
        self.results = deque(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database execute")
        return self.results.popleft()

    async def commit(self):
        raise AssertionError("Session Context service must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("Session Context service must not roll back the caller transaction")


def _session(
    *,
    tenant_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    session_type: str = "direct",
) -> ChatSession:
    return ChatSession(
        id=session_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        session_type=session_type,
        agent_id=agent_id or uuid.uuid4(),
        title="Runtime session",
        source_channel="web",
        is_primary=True,
        deleted_at=None,
    )


def _message(
    message_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    created_at: datetime,
    role: str = "user",
) -> ChatMessage:
    return ChatMessage(
        id=message_id,
        role=role,
        content=f"content:{message_id}",
        conversation_id=str(session_id),
        created_at=created_at,
        mentions=[],
    )


def _state(
    session: ChatSession,
    *,
    version: int,
    watermark: uuid.UUID | None,
    summary: str = "summary",
) -> SessionContextState:
    return SessionContextState(
        id=uuid.uuid4(),
        tenant_id=session.tenant_id,
        agent_id=None if session.session_type == "group" else session.agent_id,
        session_id=session.id,
        summary=summary,
        requirements=["keep exact wording"],
        decisions=[{"value": "LangGraph owns execution"}],
        open_items=[],
        evidence_refs=[],
        workspace_refs=["workspace://runtime"],
        covered_through_message_id=watermark,
        version=version,
    )


def _compiled(statement, *, literal_binds: bool = True):
    return statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": literal_binds},
    )


def _sql(statement) -> str:
    return str(_compiled(statement))


def test_terminal_delta_requires_the_exact_source_run_and_full_schema():
    run_id = uuid.uuid4()
    delta = service.SessionContextDelta.from_json(
        {
            "source_run_id": str(run_id),
            "new_requirements": ["keep wording"],
            "new_decisions": [{"decision": "use LangGraph"}],
            "resolved_open_items": ["old question"],
            "new_open_items": ["ship backend"],
            "evidence_refs": ["checkpoint://1"],
            "workspace_refs": ["workspace://runtime"],
            "result_summary": "Runtime design completed",
        },
        expected_source_run_id=run_id,
    )

    assert delta.source_run_id == run_id
    assert delta.new_requirements == ("keep wording",)
    assert delta.result_summary == "Runtime design completed"
    assert delta.to_json()["source_run_id"] == str(run_id)

    with pytest.raises(service.SessionContextError) as exc_info:
        service.SessionContextDelta.from_json(
            {
                **delta.to_json(),
                "source_run_id": str(uuid.uuid4()),
            },
            expected_source_run_id=run_id,
        )
    assert exc_info.value.code == "session_context_delta_source_mismatch"


@pytest.mark.asyncio
async def test_context_pack_uses_latest_state_and_recent_20_user_visible_messages():
    session = _session()
    base = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    newest_first = [
        _message(
            uuid.UUID(int=index + 1),
            session_id=session.id,
            created_at=base + timedelta(seconds=index),
            role="user" if index % 2 == 0 else "assistant",
        )
        for index in reversed(range(20))
    ]
    covered = _message(
        uuid.uuid4(),
        session_id=session.id,
        created_at=base - timedelta(seconds=2),
    )
    pending = _message(
        uuid.uuid4(),
        session_id=session.id,
        created_at=base - timedelta(seconds=1),
        role="assistant",
    )
    state = _state(
        session,
        version=4,
        watermark=covered.id,
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=state),
        _Result(scalar=covered),
        _Result(
            rows=[
                (pending, False),
                *((message, True) for message in reversed(newest_first)),
            ]
        ),
    )

    pack = await service.SessionContextService().load_context_pack(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
    )

    assert pack.snapshot.version == 4
    assert pack.snapshot.summary == "summary"
    assert len(pack.recent_messages) == 20
    assert [message["id"] for message in pack.pending_messages] == [str(pending.id)]
    assert [message["created_at"] for message in pack.recent_messages] == sorted(
        message["created_at"] for message in pack.recent_messages
    )
    recent_sql = _sql(db.statements[-1])
    assert "chat_sessions.deleted_at IS NULL" in recent_sql
    assert "chat_messages.role IN ('user', 'assistant')" in recent_sql
    assert "ORDER BY chat_messages_1.created_at DESC, chat_messages_1.id DESC" in recent_sql
    assert "LIMIT 20" in recent_sql
    assert "chat_messages.created_at >" in recent_sql
    assert "ORDER BY chat_messages.created_at ASC" in recent_sql


@pytest.mark.asyncio
async def test_group_cutoff_pack_uses_full_position_for_pending_and_recent_messages():
    session = _session(session_type="group")
    base = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    watermark = _message(
        uuid.UUID(int=10),
        session_id=session.id,
        created_at=base - timedelta(seconds=1),
    )
    lower_same_timestamp = _message(
        uuid.UUID(int=19),
        session_id=session.id,
        created_at=base,
        role="assistant",
    )
    cutoff_message = _message(
        uuid.UUID(int=20),
        session_id=session.id,
        created_at=base,
    )
    state = _state(session, version=4, watermark=watermark.id)
    # A rolling state committed exactly at the trigger timestamp is not after
    # the cutoff; the full message position remains the tie-break for messages.
    state.updated_at = base
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=state),
        _Result(scalar=cutoff_message),
        _Result(scalar=watermark),
        _Result(
            rows=[
                (lower_same_timestamp, True),
                (cutoff_message, True),
            ]
        ),
    )

    pack = await service.SessionContextService().load_context_pack_through(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        cutoff=service.MessagePosition(
            created_at=cutoff_message.created_at,
            message_id=cutoff_message.id,
        ),
    )

    assert pack.snapshot.version == 4
    assert pack.requires_transient_rebuild is False
    assert [message["id"] for message in pack.recent_messages] == [
        str(lower_same_timestamp.id),
        str(cutoff_message.id),
    ]
    cutoff_sql = _sql(db.statements[-1])
    assert "chat_messages.created_at <" in cutoff_sql
    assert "chat_messages.created_at =" in cutoff_sql
    assert "chat_messages.id <=" in cutoff_sql
    assert "ORDER BY chat_messages_1.created_at DESC, chat_messages_1.id DESC" in cutoff_sql


@pytest.mark.asyncio
async def test_group_cutoff_rebuilds_when_terminal_delta_updated_state_after_cutoff():
    session = _session(session_type="group")
    base = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    watermark = _message(
        uuid.UUID(int=10),
        session_id=session.id,
        created_at=base - timedelta(seconds=1),
    )
    cutoff_message = _message(
        uuid.UUID(int=20),
        session_id=session.id,
        created_at=base,
    )
    state = _state(
        session,
        version=5,
        watermark=watermark.id,
        summary="terminal result committed after the trigger must not leak",
    )
    # Terminal SessionContextDelta can advance the rolling state without
    # advancing its message watermark because the public reply remains recent.
    state.updated_at = base + timedelta(seconds=1)
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=state),
        _Result(scalar=cutoff_message),
        _Result(scalar=watermark),
        _Result(rows=[(watermark, False), (cutoff_message, True)]),
    )

    pack = await service.SessionContextService().load_context_pack_through(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        cutoff=service.MessagePosition(
            created_at=cutoff_message.created_at,
            message_id=cutoff_message.id,
        ),
    )

    assert pack.snapshot == service.SessionContextSnapshot.empty()
    assert pack.requires_transient_rebuild is True
    assert [message["id"] for message in pack.pending_messages] == [
        str(watermark.id)
    ]
    assert [message["id"] for message in pack.recent_messages] == [
        str(cutoff_message.id)
    ]


@pytest.mark.asyncio
async def test_group_cutoff_pack_rebuilds_when_current_compact_is_after_cutoff():
    session = _session(session_type="group")
    base = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    old_message = _message(
        uuid.UUID(int=1),
        session_id=session.id,
        created_at=base - timedelta(seconds=1),
    )
    cutoff_message = _message(
        uuid.UUID(int=2),
        session_id=session.id,
        created_at=base,
    )
    future_watermark = _message(
        uuid.UUID(int=3),
        session_id=session.id,
        created_at=base + timedelta(seconds=1),
        role="assistant",
    )
    state = _state(
        session,
        version=9,
        watermark=future_watermark.id,
        summary="must not be injected",
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=state),
        _Result(scalar=cutoff_message),
        _Result(scalar=future_watermark),
        _Result(rows=[(old_message, False), (cutoff_message, True)]),
    )

    pack = await service.SessionContextService().load_context_pack_through(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        cutoff=service.MessagePosition(
            created_at=cutoff_message.created_at,
            message_id=cutoff_message.id,
        ),
    )

    assert pack.snapshot == service.SessionContextSnapshot.empty()
    assert pack.requires_transient_rebuild is True
    assert [message["id"] for message in pack.pending_messages] == [
        str(old_message.id)
    ]
    assert [message["id"] for message in pack.recent_messages] == [
        str(cutoff_message.id)
    ]
    assert state.version == 9
    assert state.summary == "must not be injected"
    assert state.covered_through_message_id == future_watermark.id


@pytest.mark.asyncio
async def test_group_cutoff_pack_fails_closed_when_trigger_position_mismatches():
    session = _session(session_type="group")
    authoritative = _message(
        uuid.uuid4(),
        session_id=session.id,
        created_at=datetime(2026, 7, 16, 10, 0, tzinfo=UTC),
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=None),
        _Result(scalar=authoritative),
    )

    with pytest.raises(service.SessionContextError) as exc_info:
        await service.SessionContextService().load_context_pack_through(
            db,
            tenant_id=session.tenant_id,
            session_id=session.id,
            cutoff=service.MessagePosition(
                created_at=authoritative.created_at + timedelta(seconds=1),
                message_id=authoritative.id,
            ),
        )

    assert exc_info.value.code == "session_context_cutoff_mismatch"
    assert len(db.statements) == 3


@pytest.mark.asyncio
async def test_incremental_read_resolves_watermark_position_before_ordered_query():
    session = _session()
    base = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    watermark = _message(
        uuid.UUID(int=20),
        session_id=session.id,
        created_at=base,
    )
    first = _message(
        uuid.UUID(int=21),
        session_id=session.id,
        created_at=base,
        role="assistant",
    )
    second = _message(
        uuid.UUID(int=1),
        session_id=session.id,
        created_at=base + timedelta(seconds=1),
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=watermark),
        _Result(rows=[first, second]),
    )

    messages = await service.SessionContextService().load_messages_after_watermark(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        covered_through_message_id=watermark.id,
    )

    assert [message["id"] for message in messages] == [str(first.id), str(second.id)]
    watermark_sql = _sql(db.statements[1])
    incremental_sql = _sql(db.statements[2])
    assert f"chat_messages.id = '{watermark.id}'" in watermark_sql
    assert "chat_messages.created_at >" in incremental_sql
    assert "chat_messages.created_at =" in incremental_sql
    assert "chat_messages.id >" in incremental_sql
    assert "ORDER BY chat_messages.created_at ASC, chat_messages.id ASC" in incremental_sql


@pytest.mark.asyncio
async def test_compactable_read_excludes_the_latest_20_message_positions():
    session = _session(session_type="group")
    old_message = _message(
        uuid.uuid4(),
        session_id=session.id,
        created_at=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(rows=[old_message]),
    )

    messages = await service.SessionContextService().load_compactable_messages_after_watermark(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        covered_through_message_id=None,
    )

    assert [message["id"] for message in messages] == [str(old_message.id)]
    compactable_sql = _sql(db.statements[-1])
    assert "chat_messages.id NOT IN" in compactable_sql
    assert "ORDER BY chat_messages_1.created_at DESC" in compactable_sql
    assert "LIMIT 20" in compactable_sql
    assert "ORDER BY chat_messages.created_at ASC, chat_messages.id ASC" in compactable_sql


@pytest.mark.asyncio
async def test_missing_or_foreign_watermark_requires_rebuild_instead_of_guessing():
    session = _session()
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=None),
    )

    with pytest.raises(service.SessionContextError) as exc_info:
        await service.SessionContextService().load_messages_after_watermark(
            db,
            tenant_id=session.tenant_id,
            session_id=session.id,
            covered_through_message_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "session_context_rebuild_required"
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_compare_and_swap_checks_version_and_watermark_together():
    session = _session()
    base = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    old_message = _message(
        uuid.UUID(int=100),
        session_id=session.id,
        created_at=base,
    )
    new_message = _message(
        uuid.UUID(int=1),
        session_id=session.id,
        created_at=base + timedelta(seconds=1),
        role="assistant",
    )
    updated = _state(
        session,
        version=8,
        watermark=new_message.id,
        summary="updated",
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=old_message),
        _Result(scalar=new_message),
        _Result(scalar=updated),
    )

    snapshot = await service.SessionContextService().compare_and_swap(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        expected_version=7,
        expected_covered_through_message_id=old_message.id,
        candidate=service.SessionContextCandidate(
            summary="updated",
            decisions=["checkpoint is authoritative"],
            covered_through_message_id=new_message.id,
        ),
    )

    assert snapshot.version == 8
    assert snapshot.covered_through_message_id == new_message.id
    compiled = _compiled(db.statements[-1], literal_binds=False)
    cas_sql = str(compiled)
    assert "session_context_states.version =" in cas_sql
    assert "covered_through_message_id IS NOT DISTINCT FROM" in cas_sql
    assert 7 in compiled.params.values()
    assert 8 in compiled.params.values()


@pytest.mark.asyncio
async def test_stale_compare_and_swap_preserves_the_winner():
    session = _session()
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=None),
    )

    with pytest.raises(service.SessionContextConflict):
        await service.SessionContextService().compare_and_swap(
            db,
            tenant_id=session.tenant_id,
            session_id=session.id,
            expected_version=3,
            expected_covered_through_message_id=None,
            candidate=service.SessionContextCandidate(summary="stale"),
        )


@pytest.mark.asyncio
async def test_watermark_cannot_regress_even_when_uuid_is_larger():
    session = _session()
    base = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    expected = _message(
        uuid.UUID(int=1),
        session_id=session.id,
        created_at=base + timedelta(seconds=1),
    )
    candidate = _message(
        uuid.UUID(int=2**128 - 1),
        session_id=session.id,
        created_at=base,
    )
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=expected),
        _Result(scalar=candidate),
    )

    with pytest.raises(service.SessionContextError) as exc_info:
        await service.SessionContextService().compare_and_swap(
            db,
            tenant_id=session.tenant_id,
            session_id=session.id,
            expected_version=2,
            expected_covered_through_message_id=expected.id,
            candidate=service.SessionContextCandidate(
                summary="bad watermark",
                covered_through_message_id=candidate.id,
            ),
        )

    assert exc_info.value.code == "session_context_watermark_regression"
    assert len(db.statements) == 3


@pytest.mark.asyncio
async def test_first_group_context_insert_has_shared_agent_scope():
    session = _session(session_type="group")
    inserted = _state(session, version=1, watermark=None, summary="group summary")
    db = _FakeSession(
        _Result(scalar=session),
        _Result(scalar=inserted),
    )

    snapshot = await service.SessionContextService().compare_and_swap(
        db,
        tenant_id=session.tenant_id,
        session_id=session.id,
        expected_version=0,
        expected_covered_through_message_id=None,
        candidate=service.SessionContextCandidate(summary="group summary"),
    )

    assert snapshot.version == 1
    compiled = _compiled(db.statements[-1], literal_binds=False)
    insert_sql = str(compiled)
    assert "ON CONFLICT (session_id) DO NOTHING" in insert_sql
    assert "agent_id" in insert_sql
    assert None in compiled.params.values()
