"""Committed group-message projection and best-effort publisher tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.services import group_realtime
from app.services.group_message_projection import (
    GroupMessageProjection,
    build_group_message_event,
    build_group_message_payload,
    load_committed_group_message_projection,
)


NOW = datetime(2026, 7, 14, 8, 30, 0, 123456, tzinfo=UTC)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _ProjectionResult:
    def __init__(self, row=None, scalar_values=()) -> None:
        self.row = row
        self.scalar_values = scalar_values

    def one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return list(self.scalar_values)


class _ProjectionSession:
    def __init__(self, row, active_participant_ids=()) -> None:
        self.row = row
        self.active_participant_ids = active_participant_ids
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _ProjectionResult(row=self.row)
        return _ProjectionResult(scalar_values=self.active_participant_ids)


class _StatusSession(_Session):
    def __init__(self, active_participant_ids=()) -> None:
        self.active_participant_ids = active_participant_ids

    async def execute(self, _statement):
        return _ProjectionResult(scalar_values=self.active_participant_ids)


def _records():
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        agent_id=None,
        user_id=None,
        title="Runtime",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_at=NOW,
        updated_at=NOW,
    )
    participant_id = uuid.uuid4()
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=None,
        user_id=None,
        role="user",
        content="Ship the fix",
        conversation_id=str(session.id),
        participant_id=participant_id,
        mentions=[{"participant_id": str(uuid.uuid4())}],
        created_at=NOW,
    )
    return tenant_id, group_id, session, message


def test_group_message_event_matches_rest_projection_shape() -> None:
    _, group_id, session, message = _records()

    event = build_group_message_event(
        message=message,
        session=session,
        sender_name="Ada",
    )

    assert event["type"] == "message.created"
    assert event["group_id"] == str(group_id)
    assert event["session_id"] == str(session.id)
    assert event["message"] == {
        "id": str(message.id),
        "role": "user",
        "content": "Ship the fix",
        "participant_id": str(message.participant_id),
        "sender_name": "Ada",
        "mentions": message.mentions,
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "cursor": f"{NOW.isoformat()}|{message.id}",
    }
    assert event["message"] == build_group_message_payload(message, "Ada")


@pytest.mark.asyncio
async def test_projection_reloads_committed_message_and_sender() -> None:
    tenant_id, group_id, session, message = _records()
    peer_participant_id = uuid.uuid4()
    db = _ProjectionSession(
        (message, session, "Ada"),
        active_participant_ids=(message.participant_id, peer_participant_id),
    )

    projection = await load_committed_group_message_projection(
        db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        message_id=message.id,
    )

    assert projection is not None
    assert projection.tenant_id == tenant_id
    assert projection.group_id == group_id
    assert projection.session_id == session.id
    assert projection.message_id == message.id
    assert projection.active_participant_ids == (
        message.participant_id,
        peer_participant_id,
    )
    assert projection.event["message"]["sender_name"] == "Ada"  # type: ignore[index]
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_publisher_routes_committed_projection_to_whole_group(monkeypatch) -> None:
    tenant_id, group_id, session, message = _records()
    event = build_group_message_event(
        message=message,
        session=session,
        sender_name="Ada",
    )
    projection = GroupMessageProjection(
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session.id,
        message_id=message.id,
        active_participant_ids=(message.participant_id,),
        event=event,
    )
    load = AsyncMock(return_value=projection)
    route = AsyncMock()
    monkeypatch.setattr(group_realtime, "async_session", lambda: _Session())
    monkeypatch.setattr(group_realtime, "load_committed_group_message_projection", load)
    monkeypatch.setattr(group_realtime.realtime_router, "route_scope_message", route)

    await group_realtime.publish_committed_group_message(
        tenant_id=tenant_id,
        message_id=message.id,
    )

    load.assert_awaited_once()
    route.assert_awaited_once_with(
        scope_type="group",
        scope_id=str(group_id),
        message=event,
        tenant_id=str(tenant_id),
        participant_allowlist=[str(message.participant_id)],
    )


@pytest.mark.asyncio
async def test_publisher_swallows_projection_and_redis_failures(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    message_id = uuid.uuid4()
    monkeypatch.setattr(group_realtime, "async_session", lambda: _Session())
    monkeypatch.setattr(
        group_realtime,
        "load_committed_group_message_projection",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    assert (
        await group_realtime.publish_committed_group_message(
            tenant_id=tenant_id,
            message_id=message_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_runtime_status_is_ephemeral_and_membership_scoped(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    participant_ids = (uuid.uuid4(), uuid.uuid4())
    route = AsyncMock()
    monkeypatch.setattr(
        group_realtime,
        "async_session",
        lambda: _StatusSession(participant_ids),
    )
    monkeypatch.setattr(group_realtime.realtime_router, "route_scope_message", route)

    await group_realtime.publish_group_runtime_status(
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        run_id=run_id,
        status="planning",
        agent_id=agent_id,
        candidate_agent_ids=(candidate_id,),
    )

    route.assert_awaited_once_with(
        scope_type="group",
        scope_id=str(group_id),
        message={
            "type": group_realtime.GROUP_RUNTIME_STATUS_EVENT,
            "group_id": str(group_id),
            "session_id": str(session_id),
            "run_id": str(run_id),
            "status": "planning",
            "agent_id": str(agent_id),
            "candidate_agent_ids": [str(candidate_id)],
        },
        tenant_id=str(tenant_id),
        participant_allowlist=[str(value) for value in participant_ids],
    )

@pytest.mark.asyncio
async def test_membership_revoke_targets_one_participant(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    route = AsyncMock()
    monkeypatch.setattr(group_realtime.realtime_router, "route_scope_message", route)

    await group_realtime.publish_group_membership_revoked(
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
    )

    route.assert_awaited_once_with(
        scope_type="group",
        scope_id=str(group_id),
        message={
            "type": group_realtime.GROUP_MEMBERSHIP_REVOKED_EVENT,
            "group_id": str(group_id),
            "participant_id": str(participant_id),
            "code": group_realtime.GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
            "content": "Group membership was revoked",
        },
        participant_id=str(participant_id),
        tenant_id=str(tenant_id),
    )


@pytest.mark.asyncio
async def test_membership_revoke_keeps_heartbeat_as_publish_failure_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        group_realtime.realtime_router,
        "route_scope_message",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )

    assert (
        await group_realtime.publish_group_membership_revoked(
            tenant_id=uuid.uuid4(),
            group_id=uuid.uuid4(),
            participant_id=uuid.uuid4(),
        )
        is None
    )
