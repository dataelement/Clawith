"""HTTP boundary tests for native group management."""

from __future__ import annotations

from datetime import UTC, datetime
from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest

from app.api import groups as groups_api
from app.models.audit import AuditLog
from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.agent_run import AgentRun
from app.models.participant import Participant
from app.models.user import User
from app.services.group_chat_service import GroupChatServiceError, GroupSessionDeletion


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)


class _RecordingDB:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        raise AssertionError("group API must leave transaction ownership to get_db")


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
    assert ("GET", "/api/groups/{group_id}/member-candidates") in routes
    assert ("POST", "/api/groups/{group_id}/sessions") in routes
    assert ("DELETE", "/api/groups/{group_id}/sessions/{session_id}") in routes
    assert ("POST", "/api/groups/{group_id}/sessions/{session_id}/read") in routes
    assert ("GET", "/api/groups/{group_id}/sessions/{session_id}/messages") in routes
    assert ("POST", "/api/groups/{group_id}/sessions/{session_id}/messages") in routes
    assert ("GET", "/api/groups/{group_id}/sessions/{session_id}/runs") in routes
    assert ("GET", "/api/groups/{group_id}/sessions/{session_id}/runs/{run_id}") in routes
    assert ("POST", "/api/groups/{group_id}/sessions/{session_id}/runs/{run_id}/cancel") in routes
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
    assert ("PATCH", "/api/groups/{group_id}/members/{member_id}") not in routes


def test_group_invite_write_contract_only_accepts_participant_id() -> None:
    assert set(groups_api.InviteGroupMemberIn.model_fields) == {"participant_id"}


@pytest.mark.asyncio
async def test_active_group_runs_use_exact_checkpoint_status(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    session = _session(tenant_id, group.id, participant.id)
    agent_id = uuid.uuid4()
    running = SimpleNamespace(id=uuid.uuid4(), agent_id=agent_id, system_role=None)
    planning = SimpleNamespace(id=uuid.uuid4(), agent_id=None, system_role="group_planning")
    terminal = SimpleNamespace(id=uuid.uuid4(), agent_id=uuid.uuid4(), system_role=None)

    class _Scalars:
        def all(self):
            return [running, planning, terminal]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _DB(_RecordingDB):
        async def execute(self, _statement):
            return _Result()

    class _Reader:
        async def get_run_state(self, _tenant_id, run_id):
            return SimpleNamespace(
                execution_status="completed" if run_id == terminal.id else "running"
            )

    @asynccontextmanager
    async def fake_reader(_db):
        yield _Reader()

    async def fake_participant(_db, _user):
        return participant

    async def fake_authorize(*_args, **_kwargs):
        return session

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api, "_open_run_state_reader", fake_reader)
    monkeypatch.setattr(
        groups_api.group_chat_service,
        "authorize_group_session",
        fake_authorize,
    )

    result = await groups_api.list_active_group_runs(
        group.id,
        session.id,
        current_user=user,
        db=_DB(),
    )

    assert [item.run_id for item in result] == [running.id, planning.id]
    assert result[0].status == "running"
    assert result[0].can_cancel is True
    assert result[0].agent_id == agent_id
    assert result[0].system_role is None
    assert result[1].agent_id is None
    assert result[1].system_role == "group_planning"


@pytest.mark.asyncio
async def test_workspace_put_forwards_create_only_condition(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    db = _RecordingDB()
    calls = []

    async def fake_participant(_db, _user):
        return participant

    async def fake_write(_db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            path=kwargs["path"],
            content=kwargs["content"],
            exists=True,
            version_token="v1",
            modified_at="now",
            revision_id=None,
        )

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_file_service, "write_workspace_file", fake_write)

    await groups_api.put_group_workspace_file(
        group.id,
        groups_api.GroupWorkspaceFileIn(content="upload", require_absent=True),
        path="uploads/report.md",
        current_user=user,
        db=db,
    )

    assert calls[0]["require_absent"] is True
    assert "require_absent" not in groups_api.GroupTextFileIn.model_fields


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


@pytest.mark.asyncio
async def test_create_message_commits_before_realtime_publish(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    session = _session(tenant_id, group.id, participant.id)
    message_id = uuid.uuid4()
    events: list[str] = []

    class _MessageDB(_RecordingDB):
        async def commit(self) -> None:
            events.append("commit")

    db = _MessageDB()
    output = groups_api.GroupMessageOut(
        id=message_id,
        role="user",
        content="hello",
        participant_id=participant.id,
        sender_name=participant.display_name,
        mentions=[],
        created_at=NOW,
        cursor=f"{NOW.isoformat()}|{message_id}",
    )
    failure_id = uuid.uuid4()
    failure_output = groups_api.GroupMessageOut(
        id=failure_id,
        role="system",
        content="planning unavailable",
        participant_id=None,
        sender_name=None,
        mentions=[],
        created_at=NOW,
        cursor=f"{NOW.isoformat()}|{failure_id}",
    )

    async def fake_participant(_db, _user):
        return participant

    async def fake_enqueue(_db, **_kwargs):
        return SimpleNamespace(
            message=object(),
            new_public_messages=(object(), object()),
            dispatch_kind="none",
            run_handles=(),
            created=True,
            error_code=None,
        )

    async def fake_outputs(_db, _messages):
        return [output] if len(_messages) == 1 else [output, failure_output]

    async def fake_publish(**kwargs):
        assert events[0] == "commit"
        assert all(event.startswith("publish:") for event in events[1:])
        assert kwargs["group_id"] == group.id
        assert kwargs["session_id"] == session.id
        events.append(f"publish:{kwargs['message']['cursor']}")

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api.group_message_service, "enqueue_group_message", fake_enqueue)
    monkeypatch.setattr(groups_api, "_message_outputs", fake_outputs)
    monkeypatch.setattr(groups_api, "publish_group_message_created", fake_publish)

    result = await groups_api.create_group_message(
        group.id,
        session.id,
        groups_api.CreateGroupMessageIn(content="hello"),
        current_user=user,
        db=db,
    )

    assert result.message == output
    assert events == [
        "commit",
        f"publish:{output.cursor}",
        f"publish:{failure_output.cursor}",
    ]


@pytest.mark.asyncio
async def test_cancel_group_run_uses_exact_scoped_run_and_durable_command(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = _user(tenant_id)
    participant = _participant(user)
    group = _group(tenant_id, participant.id)
    session = _session(tenant_id, group.id, participant.id)
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        session_id=session.id,
        source_type="chat",
        goal="long task",
        run_kind="foreground",
        runtime_type="langgraph",
        runtime_thread_id=str(uuid.uuid4()),
        graph_name="agent_runtime",
        graph_version="test",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        delivery_status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    db = _RecordingDB()
    commands = []

    async def fake_participant(_db, _user):
        return participant

    async def fake_group_run(_db, **kwargs):
        assert kwargs == {
            "tenant_id": tenant_id,
            "group_id": group.id,
            "session_id": session.id,
            "participant_id": participant.id,
            "run_id": run.id,
        }
        return run

    @asynccontextmanager
    async def fake_reader(_db):
        class Reader:
            async def get_run_state(self, _tenant_id, _run_id):
                return SimpleNamespace(execution_status="running")

        yield Reader()

    class FakeIntake:
        def __init__(self, _db):
            assert _db is db

        async def cancel_run(self, command):
            commands.append(command)
            return SimpleNamespace(run_id=run.id)

    monkeypatch.setattr(groups_api, "_current_participant", fake_participant)
    monkeypatch.setattr(groups_api, "_authorized_group_run", fake_group_run)
    monkeypatch.setattr(groups_api, "_open_run_state_reader", fake_reader)
    monkeypatch.setattr(groups_api, "RuntimeCommandIntake", FakeIntake)

    result = await groups_api.cancel_group_run(
        group.id,
        session.id,
        run.id,
        current_user=user,
        db=db,
    )

    assert result.run_id == run.id
    assert result.status == "cancelling"
    assert result.can_cancel is False
    assert len(commands) == 1
    assert commands[0].tenant_id == tenant_id
    assert commands[0].run_id == run.id
    assert commands[0].actor_user_id == user.id
