"""Authorization and lifecycle coverage for the read-only group WebSocket."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.api import group_websocket
from app.api.group_websocket import (
    GroupSocketAuthorizationError,
    GroupSocketViewer,
    GroupWebSocketHandler,
)
from app.models.participant import Participant
from app.models.user import User
from app.services.group_chat_service import GroupChatServiceError


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, user: User | None, participant: Participant | None) -> None:
        self.user = user
        self.participant = participant

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, model, identity):
        assert model is User
        return self.user if self.user is not None and self.user.id == identity else None

    async def execute(self, _statement):
        return _Result(self.participant)


class _WebSocket:
    def __init__(self, *incoming: dict) -> None:
        self.state = SimpleNamespace()
        self.incoming = list(incoming)
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: list[int] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self) -> dict:
        if self.incoming:
            return self.incoming.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def close(self, *, code: int) -> None:
        self.closed.append(code)


@pytest.mark.asyncio
async def test_authorize_group_socket_requires_active_human_membership(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Ada",
        role="member",
        is_active=True,
    )
    participant = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user.id,
        display_name=user.display_name,
    )
    authorize = AsyncMock()
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: {"sub": str(user.id)})
    monkeypatch.setattr(group_websocket, "async_session", lambda: _Session(user, participant))
    monkeypatch.setattr(group_websocket.group_chat_service, "authorize_group_member", authorize)

    viewer = await group_websocket.authorize_group_socket_viewer(
        group_id=group_id,
        token="valid-token",
    )

    assert viewer == GroupSocketViewer(
        user_id=user.id,
        tenant_id=tenant_id,
        participant_id=participant.id,
    )
    authorize.assert_awaited_once()
    assert authorize.await_args.kwargs == {
        "tenant_id": tenant_id,
        "group_id": group_id,
        "participant_id": participant.id,
        "human_only": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_error", "expected_code"),
    [
        (GroupChatServiceError("group_not_found", "missing"), 4002),
        (GroupChatServiceError("group_access_denied", "denied"), 4003),
    ],
)
async def test_authorize_group_socket_maps_group_scope_failures(
    monkeypatch,
    service_error: GroupChatServiceError,
    expected_code: int,
) -> None:
    tenant_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        display_name="Ada",
        role="member",
        is_active=True,
    )
    participant = Participant(
        id=uuid.uuid4(),
        type="user",
        ref_id=user.id,
        display_name=user.display_name,
    )
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: {"sub": str(user.id)})
    monkeypatch.setattr(group_websocket, "async_session", lambda: _Session(user, participant))
    monkeypatch.setattr(
        group_websocket.group_chat_service,
        "authorize_group_member",
        AsyncMock(side_effect=service_error),
    )

    with pytest.raises(GroupSocketAuthorizationError) as raised:
        await group_websocket.authorize_group_socket_viewer(
            group_id=uuid.uuid4(),
            token="valid-token",
        )
    assert raised.value.close_code == expected_code


@pytest.mark.asyncio
async def test_group_socket_registers_before_connected_and_cleans_up(monkeypatch) -> None:
    group_id = uuid.uuid4()
    viewer = GroupSocketViewer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    websocket = _WebSocket({"type": "websocket.disconnect", "code": 1000})
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(
        group_websocket,
        "authorize_group_socket_viewer",
        AsyncMock(return_value=viewer),
    )
    monkeypatch.setattr(group_websocket.manager, "connect_scope", connect)
    monkeypatch.setattr(group_websocket.manager, "disconnect_scope", disconnect)

    await GroupWebSocketHandler(websocket, group_id, "token").run()  # type: ignore[arg-type]

    assert websocket.accepted is True
    connect.assert_awaited_once_with(
        scope_type="group",
        scope_id=str(group_id),
        websocket=websocket,
        user_id=str(viewer.user_id),
        participant_id=str(viewer.participant_id),
        tenant_id=str(viewer.tenant_id),
        auto_refresh=False,
    )
    assert websocket.sent[0] == {"type": "connected", "group_id": str(group_id)}
    disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_socket_transient_authorization_failure_is_retryable(monkeypatch) -> None:
    websocket = _WebSocket()
    connect = AsyncMock()
    monkeypatch.setattr(
        group_websocket,
        "authorize_group_socket_viewer",
        AsyncMock(side_effect=ConnectionError("database unavailable")),
    )
    monkeypatch.setattr(group_websocket.manager, "connect_scope", connect)

    await GroupWebSocketHandler(websocket, uuid.uuid4(), "token").run()  # type: ignore[arg-type]

    assert websocket.closed == [1011]
    assert websocket.sent[-1] == {
        "type": "error",
        "code": 1011,
        "content": "Group WebSocket setup failed",
    }
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_socket_presence_registration_failure_is_retryable(monkeypatch) -> None:
    websocket = _WebSocket()
    viewer = GroupSocketViewer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    disconnect = AsyncMock()
    monkeypatch.setattr(
        group_websocket,
        "authorize_group_socket_viewer",
        AsyncMock(return_value=viewer),
    )
    monkeypatch.setattr(
        group_websocket.manager,
        "connect_scope",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )
    monkeypatch.setattr(group_websocket.manager, "disconnect_scope", disconnect)

    await GroupWebSocketHandler(websocket, uuid.uuid4(), "token").run()  # type: ignore[arg-type]

    assert websocket.closed == [1011]
    assert websocket.sent[-1] == {
        "type": "error",
        "code": 1011,
        "content": "Group WebSocket setup failed",
    }
    disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_heartbeat_closes_when_membership_is_revoked(monkeypatch) -> None:
    websocket = _WebSocket()
    handler = GroupWebSocketHandler(websocket, uuid.uuid4(), "token")  # type: ignore[arg-type]
    handler.viewer = GroupSocketViewer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    monkeypatch.setattr(group_websocket.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        group_websocket,
        "authorize_group_socket_viewer",
        AsyncMock(side_effect=GroupSocketAuthorizationError(4003, "membership revoked")),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(group_websocket.manager, "refresh_scope", refresh)

    await handler._heartbeat_loop()

    assert websocket.closed == [4003]
    assert websocket.sent[-1]["code"] == 4003
    refresh.assert_not_awaited()
