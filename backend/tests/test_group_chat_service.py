"""Focused tests for native group chat domain invariants."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import uuid

from sqlalchemy.dialects import postgresql
import pytest

from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service


NOW = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)


class _Result:
    def __init__(self, values=None) -> None:
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalar_one(self):
        if len(self.values) != 1:
            raise AssertionError(f"expected one value, got {len(self.values)}")
        return self.values[0]

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _RecordingDB:
    def __init__(self, *results: _Result) -> None:
        self.results = deque(results)
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.popleft()

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        raise AssertionError("group service must not commit the caller transaction")

    async def rollback(self) -> None:
        raise AssertionError("group service must not roll back the caller transaction")


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _participant(participant_type: str, ref_id: uuid.UUID) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type=participant_type,
        ref_id=ref_id,
        display_name="Member",
    )


def _group(
    tenant_id: uuid.UUID,
    creator_participant_id: uuid.UUID,
) -> Group:
    return Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Runtime Group",
        created_by_participant_id=creator_participant_id,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _membership(
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
    *,
    role: str = "member",
    read_state: dict | None = None,
) -> GroupMember:
    return GroupMember(
        id=uuid.uuid4(),
        group_id=group_id,
        participant_id=participant_id,
        role=role,
        joined_at=NOW,
        removed_at=None,
        session_read_state=read_state or {},
    )


def _session(
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    creator_participant_id: uuid.UUID,
    *,
    primary: bool,
    last_message_at: datetime | None = None,
) -> ChatSession:
    return ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=creator_participant_id,
        title="Session",
        source_channel="web",
        is_group=True,
        is_primary=primary,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
        last_message_at=last_message_at,
    )


def _message(
    session_id: uuid.UUID,
    *,
    created_at: datetime,
    participant_id: uuid.UUID | None = None,
) -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4(),
        role="user",
        content="message",
        conversation_id=str(session_id),
        participant_id=participant_id,
        mentions=[],
        created_at=created_at,
    )


def _agent(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    access_mode: str = "company",
) -> Agent:
    return Agent(
        id=agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Group Agent",
        status="idle",
        is_expired=False,
        access_mode=access_mode,
    )


@pytest.mark.asyncio
async def test_create_group_stages_the_human_creator_as_manager() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    creator = _participant("user", user_id)
    db = _RecordingDB(_Result([creator]), _Result([user_id]))

    group = await group_chat_service.create_group(
        db,
        tenant_id=tenant_id,
        creator_participant_id=creator.id,
        name="  Product launch  ",
        description="Coordinate the launch",
    )

    assert group.name == "Product launch"
    assert group.tenant_id == tenant_id
    assert len(db.added) == 2
    membership = next(value for value in db.added if isinstance(value, GroupMember))
    assert membership.group_id == group.id
    assert membership.participant_id == creator.id
    assert membership.role == "manager"
    assert membership.session_read_state == {}
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_ordinary_human_member_can_invite_a_company_agent() -> None:
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    actor = _participant("user", actor_user_id)
    group = _group(tenant_id, actor.id)
    actor_membership = _membership(group.id, actor.id)
    actor_user = User(
        id=actor_user_id,
        tenant_id=tenant_id,
        display_name="Group Member",
        role="member",
        is_active=True,
    )
    agent_id = uuid.uuid4()
    invited = _participant("agent", agent_id)
    target_agent = _agent(tenant_id, agent_id)
    db = _RecordingDB(
        _Result([group]),
        _Result([actor_membership]),
        _Result([actor]),
        _Result([actor_user_id]),
        _Result([invited]),
        _Result([target_agent]),
        _Result([actor_user]),
        _Result([target_agent]),
        _Result(),
    )

    membership = await group_chat_service.invite_group_member(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=actor.id,
        participant_id=invited.id,
    )

    assert membership.role == "member"
    assert membership.participant_id == invited.id
    assert db.added == [membership]
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_private_agent_cannot_be_invited() -> None:
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    actor = _participant("user", actor_user_id)
    group = _group(tenant_id, actor.id)
    actor_membership = _membership(group.id, actor.id)
    agent_id = uuid.uuid4()
    invited = _participant("agent", agent_id)
    db = _RecordingDB(
        _Result([group]),
        _Result([actor_membership]),
        _Result([actor]),
        _Result([actor_user_id]),
        _Result([invited]),
        _Result([_agent(tenant_id, agent_id, access_mode="private")]),
    )

    with pytest.raises(group_chat_service.GroupChatServiceError) as exc_info:
        await group_chat_service.invite_group_member(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            actor_participant_id=actor.id,
            participant_id=invited.id,
        )

    assert exc_info.value.code == "group_participant_invalid"
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_member_candidates_materialize_backend_participant_ids_and_exclude_active_users(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    actor = _participant("user", actor_user_id)
    actor_user = User(
        id=actor_user_id,
        tenant_id=tenant_id,
        display_name="Group Member",
        role="member",
        is_active=True,
    )
    group = _group(tenant_id, actor.id)
    candidate_user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Candidate User",
        title="Researcher",
        role="member",
        is_active=True,
    )
    candidate_participant = _participant("user", candidate_user.id)
    active_user_id = uuid.uuid4()
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id)]),
        _Result([actor]),
        _Result([actor_user_id]),
        _Result([active_user_id]),
        _Result([candidate_user]),
    )

    async def fake_get_or_create(_db, user_id, display_name, avatar_url):
        assert _db is db
        assert (user_id, display_name, avatar_url) == (
            candidate_user.id,
            candidate_user.display_name,
            candidate_user.avatar_url,
        )
        return candidate_participant

    monkeypatch.setattr(
        group_chat_service,
        "get_or_create_user_participant",
        fake_get_or_create,
    )

    candidates = await group_chat_service.list_group_member_candidates(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=actor.id,
        actor_user=actor_user,
        participant_type="user",
        limit=50,
    )

    assert candidates == (
        group_chat_service.GroupMemberCandidate(
            participant_id=candidate_participant.id,
            participant_type="user",
            participant_ref_id=candidate_user.id,
            display_name="Candidate User",
            avatar_url=None,
            title="Researcher",
        ),
    )
    candidate_sql = _sql(db.statements[-1])
    assert "users.tenant_id" in candidate_sql
    assert "users.is_active IS true" in candidate_sql
    assert str(active_user_id) in candidate_sql


@pytest.mark.asyncio
async def test_agent_candidates_apply_visibility_and_runtime_eligibility_filters(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    actor = _participant("user", actor_user_id)
    actor_user = User(
        id=actor_user_id,
        tenant_id=tenant_id,
        display_name="Group Member",
        role="member",
        is_active=True,
    )
    group = _group(tenant_id, actor.id)
    candidate_agent = _agent(tenant_id, uuid.uuid4(), access_mode="company")
    candidate_participant = _participant("agent", candidate_agent.id)
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id)]),
        _Result([actor]),
        _Result([actor_user_id]),
        _Result(),
        _Result([candidate_agent]),
    )

    async def fake_get_or_create(_db, agent_id, display_name, avatar_url):
        assert _db is db
        assert (agent_id, display_name, avatar_url) == (
            candidate_agent.id,
            candidate_agent.name,
            candidate_agent.avatar_url,
        )
        return candidate_participant

    monkeypatch.setattr(
        group_chat_service,
        "get_or_create_agent_participant",
        fake_get_or_create,
    )

    candidates = await group_chat_service.list_group_member_candidates(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=actor.id,
        actor_user=actor_user,
        participant_type="agent",
        limit=50,
    )

    assert [candidate.participant_id for candidate in candidates] == [candidate_participant.id]
    candidate_sql = _sql(db.statements[-1])
    assert "agents.access_mode != 'private'" in candidate_sql
    assert "agents.status IN ('creating', 'running', 'idle')" in candidate_sql
    assert "agents.is_expired IS false" in candidate_sql


@pytest.mark.asyncio
async def test_invisible_custom_agent_cannot_be_invited_by_guessed_participant_id(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    actor = _participant("user", actor_user_id)
    actor_user = User(
        id=actor_user_id,
        tenant_id=tenant_id,
        display_name="Group Member",
        role="member",
        is_active=True,
    )
    group = _group(tenant_id, actor.id)
    actor_membership = _membership(group.id, actor.id)
    target_agent_id = uuid.uuid4()
    target_participant = _participant("agent", target_agent_id)
    target_agent = _agent(tenant_id, target_agent_id, access_mode="custom")
    db = _RecordingDB(
        _Result([group]),
        _Result([actor_membership]),
        _Result([actor]),
        _Result([actor_user_id]),
        _Result([target_participant]),
        _Result([target_agent]),
        _Result([actor_user]),
        _Result([target_agent]),
    )

    async def fake_can_use_agent(_db, user, agent):
        assert _db is db
        assert user is actor_user
        assert agent is target_agent
        return False

    monkeypatch.setattr(
        group_chat_service,
        "can_use_agent",
        fake_can_use_agent,
        raising=False,
    )

    with pytest.raises(group_chat_service.GroupChatServiceError) as exc_info:
        await group_chat_service.invite_group_member(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            actor_participant_id=actor.id,
            participant_id=target_participant.id,
        )

    assert exc_info.value.code == "group_participant_invalid"
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_first_group_session_uses_unified_group_flags_and_becomes_primary() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id)]),
        _Result([actor]),
        _Result([user_id]),
        _Result(),
    )

    session = await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=actor.id,
    )

    assert session.session_type == "group"
    assert session.is_group is True
    assert session.group_id == group.id
    assert session.agent_id is None
    assert session.user_id is None
    assert session.is_primary is True


@pytest.mark.asyncio
async def test_deleting_the_last_group_session_leaves_no_primary_and_cancels_collaboration(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    session = _session(tenant_id, group.id, actor.id, primary=True)
    cancelled = (uuid.uuid4(), uuid.uuid4())
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id, role="manager")]),
        _Result([actor]),
        _Result([user_id]),
        _Result([session]),
        _Result(),
    )
    cancel_calls = []

    async def fake_cancel(_db, **kwargs):
        cancel_calls.append(kwargs)
        return cancelled

    monkeypatch.setattr(
        group_chat_service,
        "enqueue_session_deletion_cancels",
        fake_cancel,
    )

    result = await group_chat_service.soft_delete_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        session_id=session.id,
        actor_participant_id=actor.id,
    )

    assert result.session is session
    assert result.replacement is None
    assert result.cancelled_run_ids == cancelled
    assert session.deleted_at is not None
    assert session.is_primary is False
    assert cancel_calls == [
        {
            "tenant_id": tenant_id,
            "session_id": session.id,
            "actor_user_id": user_id,
        }
    ]
    election_sql = _sql(db.statements[5])
    assert "chat_sessions.last_message_at DESC NULLS LAST" in election_sql
    assert "chat_sessions.created_at DESC" in election_sql
    assert "chat_sessions.id DESC" in election_sql


@pytest.mark.asyncio
async def test_deleting_primary_promotes_the_most_recent_remaining_session(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    session = _session(tenant_id, group.id, actor.id, primary=True)
    replacement = _session(
        tenant_id,
        group.id,
        actor.id,
        primary=False,
        last_message_at=NOW + timedelta(minutes=1),
    )
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id, role="manager")]),
        _Result([actor]),
        _Result([user_id]),
        _Result([session]),
        _Result([replacement]),
    )

    async def fake_cancel(_db, **kwargs):
        del kwargs
        return ()

    monkeypatch.setattr(
        group_chat_service,
        "enqueue_session_deletion_cancels",
        fake_cancel,
    )

    result = await group_chat_service.soft_delete_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        session_id=session.id,
        actor_participant_id=actor.id,
    )

    assert result.replacement is replacement
    assert replacement.is_primary is True
    assert result.cancelled_run_ids == ()


@pytest.mark.asyncio
async def test_disbanding_group_cancels_foreground_collaboration_in_every_session(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    first_session = _session(tenant_id, group.id, actor.id, primary=True)
    second_session = _session(tenant_id, group.id, actor.id, primary=False)
    db = _RecordingDB(
        _Result([group]),
        _Result([_membership(group.id, actor.id, role="manager")]),
        _Result([actor]),
        _Result([user_id]),
        _Result([first_session.id, second_session.id]),
        _Result(),
        _Result(),
    )
    cancel_calls = []

    async def fake_cancel(_db, **kwargs):
        cancel_calls.append(kwargs)
        return ()

    monkeypatch.setattr(
        group_chat_service,
        "enqueue_session_deletion_cancels",
        fake_cancel,
    )

    deleted = await group_chat_service.soft_delete_group(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=actor.id,
    )

    assert deleted is group
    assert group.deleted_at is not None
    assert cancel_calls == [
        {
            "tenant_id": tenant_id,
            "session_id": first_session.id,
            "actor_user_id": user_id,
        },
        {
            "tenant_id": tenant_id,
            "session_id": second_session.id,
            "actor_user_id": user_id,
        },
    ]
    session_select_sql = _sql(db.statements[4])
    assert "chat_sessions.deleted_at IS NULL" in session_select_sql
    assert "chat_sessions.created_at" in session_select_sql
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_delayed_read_request_cannot_move_a_session_watermark_backwards() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    session = _session(tenant_id, group.id, actor.id, primary=True)
    old_message = _message(session.id, created_at=NOW)
    delayed_message = _message(session.id, created_at=NOW - timedelta(minutes=1))
    membership = _membership(
        group.id,
        actor.id,
        read_state={
            str(session.id): {
                "last_read_message_id": str(old_message.id),
                "last_read_at": NOW.isoformat(),
            }
        },
    )
    db = _RecordingDB(
        _Result([group]),
        _Result([membership]),
        _Result([actor]),
        _Result([user_id]),
        _Result([session]),
        _Result([delayed_message]),
        _Result([old_message]),
    )

    result = await group_chat_service.mark_group_session_read(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        session_id=session.id,
        participant_id=actor.id,
        message_id=delayed_message.id,
    )

    assert result.advanced is False
    assert result.last_read_message_id == old_message.id
    assert membership.session_read_state[str(session.id)]["last_read_message_id"] == str(old_message.id)
    assert db.flush_count == 0
    membership_sql = _sql(db.statements[1])
    assert "FOR UPDATE" in membership_sql


@pytest.mark.asyncio
async def test_unread_count_uses_message_position_and_excludes_the_reader() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    actor = _participant("user", user_id)
    group = _group(tenant_id, actor.id)
    session = _session(tenant_id, group.id, actor.id, primary=True)
    watermark = _message(session.id, created_at=NOW, participant_id=actor.id)
    membership = _membership(
        group.id,
        actor.id,
        read_state={
            str(session.id): {
                "last_read_message_id": str(watermark.id),
                "last_read_at": NOW.isoformat(),
            }
        },
    )
    db = _RecordingDB(
        _Result([group]),
        _Result([membership]),
        _Result([actor]),
        _Result([user_id]),
        _Result([session]),
        _Result([watermark]),
        _Result([3]),
    )

    count = await group_chat_service.get_group_session_unread_count(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        session_id=session.id,
        participant_id=actor.id,
    )

    assert count == 3
    count_sql = _sql(db.statements[-1])
    assert f"chat_messages.conversation_id = '{session.id}'" in count_sql
    assert "chat_messages.created_at >" in count_sql
    assert "chat_messages.created_at =" in count_sql
    assert "chat_messages.id >" in count_sql
    assert f"chat_messages.participant_id != '{actor.id}'" in count_sql
