"""HTTP boundary tests for native group management."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from app.api import groups as groups_api
from app.models.agent import Agent
from app.models.audit import AuditLog, ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services.group_chat_service import GroupChatServiceError, GroupSessionDeletion
from app.services.group_message_service import GroupMessageIntake


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RecordingDB:
    def __init__(self, *, results=()) -> None:
        self.added = []
        self.results = deque(results)
        self.statements = []

    def add(self, value) -> None:
        self.added.append(value)

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.results:
            raise AssertionError("unexpected database query")
        return _ScalarResult(self.results.popleft())

    async def commit(self) -> None:
        raise AssertionError("group API must leave transaction ownership to get_db")


class _ChunkedUpload:
    def __init__(self, content: bytes) -> None:
        self.filename = "evidence.bin"
        self.content_type = "application/octet-stream"
        self._content = content
        self._offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            size = len(self._content) - self._offset
        start = self._offset
        self._offset = min(len(self._content), self._offset + size)
        return self._content[start:self._offset]


def _user(tenant_id: uuid.UUID) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Group Owner",
        avatar_url=None,
        role="member",
        is_active=True,
    )


def _participant(user: User) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user.id,
        display_name=user.display_name,
    )


def _agent(user: User) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        creator_id=user.id,
        name="Group Analyst",
        avatar_url=None,
        primary_model_id=None,
        status="idle",
        is_expired=False,
        access_mode="company",
    )


def _group(tenant_id: uuid.UUID, participant_id: uuid.UUID) -> Group:
    return Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Runtime Group",
        description=None,
        created_by_participant_id=participant_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _session(tenant_id: uuid.UUID, group_id: uuid.UUID, participant_id: uuid.UUID) -> ChatSession:
    return ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=participant_id,
        title="Runtime",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_at=NOW,
        updated_at=NOW,
    )


def test_group_router_exposes_management_and_read_state_boundaries() -> None:
    routes = {
        (method, route.path)
        for route in groups_api.router.routes
        for method in (route.methods or set())
    }

    assert ("POST", "/api/groups") in routes
    assert ("GET", "/api/groups/{group_id}/members") in routes
    assert ("POST", "/api/groups/{group_id}/sessions") in routes
    assert ("DELETE", "/api/groups/{group_id}/sessions/{session_id}") in routes
    assert ("POST", "/api/groups/{group_id}/sessions/{session_id}/read") in routes
    assert ("GET", "/api/groups/{group_id}/sessions/{session_id}/messages") in routes
    assert ("POST", "/api/groups/{group_id}/sessions/{session_id}/messages") in routes
    assert ("GET", "/api/groups/{group_id}/announcement") in routes
    assert ("PUT", "/api/groups/{group_id}/announcement") in routes
    assert ("GET", "/api/groups/{group_id}/agents/{agent_id}/memory") in routes
    assert ("PUT", "/api/groups/{group_id}/agents/{agent_id}/memory") in routes
    assert ("DELETE", "/api/groups/{group_id}/agents/{agent_id}/memory") in routes
    assert ("GET", "/api/groups/{group_id}/sessions/{session_id}/summary") in routes
    assert ("GET", "/api/groups/{group_id}/workspace") in routes
    assert ("GET", "/api/groups/{group_id}/workspace/file") in routes
    assert ("PUT", "/api/groups/{group_id}/workspace/file") in routes
    assert ("DELETE", "/api/groups/{group_id}/workspace/file") in routes
    assert ("POST", "/api/groups/{group_id}/workspace/upload") in routes
    assert ("GET", "/api/groups/{group_id}/workspace/download") in routes
    assert ("PATCH", "/api/groups/{group_id}/members/{member_id}") not in routes


@pytest.mark.asyncio
async def test_workspace_upload_rejects_oversize_before_storage_or_revision(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()
    upload = _ChunkedUpload(b"123456789")
    write = AsyncMock()

    async def fake_participant(_db, _user):
        assert _db is db
        assert _user is user
        return participant

    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_MAX_BYTES", 8)
    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_CHUNK_BYTES", 3)
    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_chat_service,
        "authorize_group_member",
        AsyncMock(),
    )
    monkeypatch.setattr(
        groups_api.group_file_service,
        "upload_workspace_file",
        write,
    )

    with pytest.raises(HTTPException) as exc_info:
        await groups_api.upload_group_workspace_file(
            group_id=uuid.uuid4(),
            file=upload,  # type: ignore[arg-type]
            path="evidence",
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == {
        "code": "group_workspace_upload_too_large",
        "message": "Group workspace uploads must be 8 bytes or smaller",
    }
    assert upload.read_sizes == [3, 3, 3]
    write.assert_not_awaited()
    assert db.added == []


@pytest.mark.asyncio
async def test_workspace_upload_authorizes_membership_before_reading(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()
    upload = _ChunkedUpload(b"payload")
    write = AsyncMock()

    async def fake_participant(_db, _user):
        return participant

    async def deny_membership(_db, **_kwargs):
        raise GroupChatServiceError(
            "group_access_denied",
            "Active group membership is required",
        )

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_chat_service,
        "authorize_group_member",
        deny_membership,
    )
    monkeypatch.setattr(
        groups_api.group_file_service,
        "upload_workspace_file",
        write,
    )

    with pytest.raises(HTTPException) as exc_info:
        await groups_api.upload_group_workspace_file(
            group_id=uuid.uuid4(),
            file=upload,  # type: ignore[arg-type]
            path="evidence",
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert upload.read_sizes == []
    write.assert_not_awaited()
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        groups_api.InviteGroupMemberIn(),
        groups_api.InviteGroupMemberIn(participant_type="user"),
        groups_api.InviteGroupMemberIn(ref_id=uuid.uuid4()),
        groups_api.InviteGroupMemberIn(
            participant_id=uuid.uuid4(),
            participant_type="agent",
            ref_id=uuid.uuid4(),
        ),
    ],
)
async def test_invite_identity_rejects_incomplete_or_mixed_protocols(body) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    db = _RecordingDB()

    with pytest.raises(HTTPException) as exc_info:
        await groups_api._resolve_invited_participant(
            db,  # type: ignore[arg-type]
            current_user=user,
            tenant_id=tenant_id,
            body=body,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "group_invitee_identity_invalid"
    assert db.statements == []


@pytest.mark.asyncio
async def test_invite_identity_resolves_legacy_participant_to_active_tenant_user(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    legacy = _participant(user)
    canonical = _participant(user)
    db = _RecordingDB(results=(legacy, user))
    calls = []

    async def fake_user_participant(_db, user_id, display_name, avatar_url):
        calls.append((_db, user_id, display_name, avatar_url))
        return canonical

    monkeypatch.setattr(
        groups_api,
        "get_or_create_user_participant",
        fake_user_participant,
    )

    result = await groups_api._resolve_invited_participant(
        db,  # type: ignore[arg-type]
        current_user=user,
        tenant_id=tenant_id,
        body=groups_api.InviteGroupMemberIn(participant_id=legacy.id),
    )

    assert result is canonical
    assert calls == [(db, user.id, user.display_name, user.avatar_url)]
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_invite_identity_resolves_business_user(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    canonical = _participant(user)
    db = _RecordingDB(results=(user,))
    calls = []

    async def fake_user_participant(_db, user_id, display_name, avatar_url):
        calls.append((_db, user_id, display_name, avatar_url))
        return canonical

    monkeypatch.setattr(
        groups_api,
        "get_or_create_user_participant",
        fake_user_participant,
    )

    result = await groups_api._resolve_invited_participant(
        db,  # type: ignore[arg-type]
        current_user=user,
        tenant_id=tenant_id,
        body=groups_api.InviteGroupMemberIn(
            participant_type="user",
            ref_id=user.id,
        ),
    )

    assert result is canonical
    assert calls == [(db, user.id, user.display_name, user.avatar_url)]
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_invite_identity_resolves_available_business_agent(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    agent = _agent(user)
    canonical = Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=agent.id,
        display_name=agent.name,
    )
    db = _RecordingDB(results=(agent,))
    access_calls = []
    participant_calls = []

    async def fake_access(_db, user_id, value):
        access_calls.append((_db, user_id, value))
        return "use"

    async def fake_agent_participant(_db, agent_id, name, avatar_url):
        participant_calls.append((_db, agent_id, name, avatar_url))
        return canonical

    monkeypatch.setattr(groups_api, "get_agent_access_level_for_user_id", fake_access)
    monkeypatch.setattr(groups_api, "is_agent_expired", lambda _agent: False)
    monkeypatch.setattr(
        groups_api,
        "get_or_create_agent_participant",
        fake_agent_participant,
    )

    result = await groups_api._resolve_invited_participant(
        db,  # type: ignore[arg-type]
        current_user=user,
        tenant_id=tenant_id,
        body=groups_api.InviteGroupMemberIn(
            participant_type="agent",
            ref_id=agent.id,
        ),
    )

    assert result is canonical
    assert access_calls == [(db, user.id, agent)]
    assert participant_calls == [(db, agent.id, agent.name, agent.avatar_url)]


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable_reason", ["private", "stopped", "expired", "no_access"])
async def test_invite_identity_rejects_unavailable_business_agent(
    monkeypatch,
    unavailable_reason,
) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    agent = _agent(user)
    if unavailable_reason == "private":
        agent.access_mode = "private"
    if unavailable_reason == "stopped":
        agent.status = "stopped"
    db = _RecordingDB(results=(agent,))

    async def fake_access(_db, _user_id, _agent):
        return None if unavailable_reason == "no_access" else "use"

    monkeypatch.setattr(groups_api, "get_agent_access_level_for_user_id", fake_access)
    monkeypatch.setattr(
        groups_api,
        "is_agent_expired",
        lambda _agent: unavailable_reason == "expired",
    )

    with pytest.raises(HTTPException) as exc_info:
        await groups_api._resolve_invited_participant(
            db,  # type: ignore[arg-type]
            current_user=user,
            tenant_id=tenant_id,
            body=groups_api.InviteGroupMemberIn(
                participant_type="agent",
                ref_id=agent.id,
            ),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "code": "group_invitee_unavailable",
        "message": "Agent is not available to join this group",
    }


@pytest.mark.asyncio
async def test_invite_authorizes_human_member_before_resolving_target(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()
    resolve = AsyncMock()
    invite = AsyncMock()

    async def fake_participant(_db, _user):
        return participant

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_chat_service,
        "authorize_group_member",
        AsyncMock(
            side_effect=GroupChatServiceError(
                "group_access_denied",
                "Active group membership is required",
            )
        ),
    )
    monkeypatch.setattr(groups_api, "_resolve_invited_participant", resolve)
    monkeypatch.setattr(groups_api.group_chat_service, "invite_group_member", invite)

    with pytest.raises(HTTPException) as exc_info:
        await groups_api.invite_group_member(
            group_id=group_id,
            body=groups_api.InviteGroupMemberIn(
                participant_type="user",
                ref_id=uuid.uuid4(),
            ),
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "group_access_denied"
    groups_api.group_chat_service.authorize_group_member.assert_awaited_once_with(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant.id,
        human_only=True,
    )
    resolve.assert_not_awaited()
    invite.assert_not_awaited()
    assert db.statements == []


@pytest.mark.asyncio
async def test_remove_member_publishes_revoke_only_after_commit(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    member_id = uuid.uuid4()
    user = _user(tenant_id)
    actor = _participant(user)
    removed = GroupMember(
        id=member_id,
        group_id=group_id,
        participant_id=uuid.uuid4(),
        role="member",
        joined_at=NOW,
        removed_at=NOW,
        session_read_state={},
    )
    db = _RecordingDB()
    staged_callbacks = []
    published = []

    async def fake_participant(_db, _user):
        return actor

    async def fake_remove(_db, **kwargs):
        assert _db is db
        assert kwargs == {
            "tenant_id": tenant_id,
            "group_id": group_id,
            "actor_participant_id": actor.id,
            "member_id": member_id,
        }
        return removed

    def fake_stage(_db, callback):
        assert _db is db
        staged_callbacks.append(callback)

    async def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_chat_service,
        "remove_group_member",
        fake_remove,
    )
    monkeypatch.setattr(groups_api, "add_after_commit_callback", fake_stage)
    monkeypatch.setattr(groups_api, "publish_group_membership_revoked", fake_publish)

    result = await groups_api.remove_group_member(
        group_id=group_id,
        member_id=member_id,
        current_user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert result is None
    assert len(staged_callbacks) == 1
    assert published == []

    await staged_callbacks[0]()

    assert published == [
        {
            "tenant_id": tenant_id,
            "group_id": group_id,
            "participant_id": removed.participant_id,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor_name", ["before", "after"])
async def test_list_group_messages_passes_each_cursor_to_service(
    monkeypatch,
    cursor_name,
) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    cursor = f"{NOW.isoformat()}|{message_id}"
    calls = []
    marker = object()

    async def fake_participant(_db, _user):
        return participant

    async def fake_list(_db, **kwargs):
        calls.append((_db, kwargs))
        return []

    async def fake_outputs(_db, messages):
        assert _db is db
        assert messages == []
        return [marker]

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_message_service,
        "list_group_messages",
        fake_list,
    )
    monkeypatch.setattr(groups_api, "_message_outputs", fake_outputs)
    cursor_kwargs = {"before": None, "after": None}
    cursor_kwargs[cursor_name] = cursor

    result = await groups_api.list_group_messages(
        group_id=group_id,
        session_id=session_id,
        limit=50,
        current_user=user,
        db=db,  # type: ignore[arg-type]
        **cursor_kwargs,
    )

    expected_cursor = (NOW, message_id)
    assert result == [marker]
    assert calls == [
        (
            db,
            {
                "tenant_id": tenant_id,
                "group_id": group_id,
                "session_id": session_id,
                "viewer_participant_id": participant.id,
                "limit": 50,
                "before": expected_cursor if cursor_name == "before" else None,
                "after": expected_cursor if cursor_name == "after" else None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_list_group_messages_rejects_before_and_after_together() -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    db = _RecordingDB()
    cursor = f"{NOW.isoformat()}|{uuid.uuid4()}"

    with pytest.raises(HTTPException) as exc_info:
        await groups_api.list_group_messages(
            group_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            limit=20,
            before=cursor,
            after=cursor,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Message cursors 'before' and 'after' are mutually exclusive"
    )
    assert db.statements == []


@pytest.mark.asyncio
async def test_create_group_message_only_stages_broadcast_after_commit(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        role="user",
        content="Persist this before broadcasting",
        conversation_id=str(session_id),
        participant_id=participant.id,
        user_id=user.id,
        mentions=[],
        created_at=NOW,
    )
    intake = GroupMessageIntake(
        message=message,
        mentions=(),
        dispatch_kind="none",
        run_handles=(),
        created=True,
    )
    output = groups_api.GroupMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        participant_id=participant.id,
        sender_name=participant.display_name,
        mentions=[],
        created_at=NOW,
        cursor=f"{NOW.isoformat()}|{message.id}",
    )
    staged_callbacks = []
    published = []

    async def fake_participant(_db, _user):
        return participant

    async def fake_enqueue(_db, **kwargs):
        assert _db is db
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["group_id"] == group_id
        assert kwargs["session_id"] == session_id
        return intake

    async def fake_outputs(_db, messages):
        assert _db is db
        assert messages == [message]
        return [output]

    def fake_stage(_db, callback):
        assert _db is db
        staged_callbacks.append(callback)

    async def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(
        groups_api.group_message_service,
        "enqueue_group_message",
        fake_enqueue,
    )
    monkeypatch.setattr(groups_api, "_message_outputs", fake_outputs)
    monkeypatch.setattr(groups_api, "add_after_commit_callback", fake_stage)
    monkeypatch.setattr(groups_api, "publish_committed_group_message", fake_publish)

    result = await groups_api.create_group_message(
        group_id=group_id,
        session_id=session_id,
        body=groups_api.CreateGroupMessageIn(content=message.content),
        current_user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert result.message == output
    assert result.created is True
    assert len(staged_callbacks) == 1
    assert published == []

    await staged_callbacks[0]()

    assert published == [{"tenant_id": tenant_id, "message_id": message.id}]


@pytest.mark.asyncio
async def test_create_group_stages_domain_change_and_audit_in_one_transaction(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    db = _RecordingDB()
    calls = []

    async def fake_participant(_db, current_user):
        assert _db is db
        assert current_user is user
        return participant

    async def fake_create(_db, **kwargs):
        calls.append(kwargs)
        return group

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_chat_service, "create_group", fake_create)

    result = await groups_api.create_group(
        groups_api.CreateGroupIn(name="Runtime Group"),
        current_user=user,
        db=db,
    )

    assert result is group
    assert calls == [
        {
            "tenant_id": tenant_id,
            "creator_participant_id": participant.id,
            "name": "Runtime Group",
            "description": None,
        }
    ]
    assert len(db.added) == 1
    audit = db.added[0]
    assert isinstance(audit, AuditLog)
    assert audit.action == "group:create"
    assert audit.user_id == user.id
    assert audit.details == {"tenant_id": str(tenant_id), "group_id": str(group.id)}


@pytest.mark.asyncio
async def test_patch_group_preserves_explicit_description_clear(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    group.description = "old"
    db = _RecordingDB()
    calls = []

    async def fake_participant(_db, _user):
        return participant

    async def fake_update(_db, **kwargs):
        calls.append(kwargs)
        group.description = kwargs["description"]
        return group

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_chat_service, "update_group", fake_update)

    result = await groups_api.patch_group(
        group.id,
        groups_api.PatchGroupIn(description=None),
        current_user=user,
        db=db,
    )

    assert result.description is None
    assert calls[0]["name"] is None
    assert calls[0]["description"] is None
    assert calls[0]["update_description"] is True
    assert db.added[0].details["fields"] == ["description"]


@pytest.mark.asyncio
async def test_delete_group_session_audits_replacement_without_committing(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    deleted = _session(tenant_id, group.id, participant.id)
    replacement = _session(tenant_id, group.id, participant.id)
    cancelled_run_ids = (uuid.uuid4(), uuid.uuid4())
    db = _RecordingDB()

    async def fake_participant(_db, _user):
        return participant

    async def fake_delete(_db, **kwargs):
        assert kwargs["session_id"] == deleted.id
        return GroupSessionDeletion(
            session=deleted,
            replacement=replacement,
            cancelled_run_ids=cancelled_run_ids,
        )

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_chat_service, "soft_delete_group_session", fake_delete)

    result = await groups_api.delete_group_session(
        group.id,
        deleted.id,
        current_user=user,
        db=db,
    )

    assert result is None
    audit = db.added[0]
    assert audit.action == "group:session_delete"
    assert audit.details["replacement_session_id"] == str(replacement.id)
    assert audit.details["cancelled_run_count"] == 2


@pytest.mark.asyncio
async def test_domain_failure_is_returned_as_stable_http_error(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    db = _RecordingDB()

    async def fake_participant(_db, _user):
        return participant

    async def fake_get(_db, **_kwargs):
        raise GroupChatServiceError("group_access_denied", "Membership is required")

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_chat_service, "get_group", fake_get)

    with pytest.raises(HTTPException) as exc_info:
        await groups_api.get_group(
            uuid.uuid4(),
            current_user=user,
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "code": "group_access_denied",
        "message": "Membership is required",
    }
