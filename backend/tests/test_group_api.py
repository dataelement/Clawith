"""HTTP boundary tests for native group management."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from fastapi import HTTPException
import pytest

from app.api import groups as groups_api
from app.models.audit import AuditLog
from app.models.chat_session import ChatSession
from app.models.group import Group
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
    assert ("PATCH", "/api/groups/{group_id}/members/{member_id}") not in routes


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
