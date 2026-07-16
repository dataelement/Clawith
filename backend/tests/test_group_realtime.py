"""Native Group websocket and message event contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import WebSocketDisconnect
import pytest

from app.api import group_websocket
from app.models.audit import ChatMessage
from app.services.group_realtime import (
    group_connection_key,
    group_message_payload,
    publish_group_message_created,
)


NOW = datetime(2026, 7, 16, 9, 30, tzinfo=UTC)


class _WebSocket:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.accepted = False
        self.sent: list[dict] = []
        self.closed_with: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, *, code: int) -> None:
        self.closed_with = code

    async def receive_json(self) -> dict:
        raise WebSocketDisconnect()


class _TimeoutWebSocket(_WebSocket):
    async def receive_json(self) -> dict:
        raise TimeoutError()


def _message() -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4(),
        role="assistant",
        content="done",
        conversation_id=str(uuid.uuid4()),
        participant_id=uuid.uuid4(),
        mentions=[{"participant_id": str(uuid.uuid4())}],
        created_at=NOW,
    )


def test_group_websocket_route_is_exposed() -> None:
    assert "/ws/group/{group_id}" in {route.path for route in group_websocket.router.routes}


def test_group_message_payload_matches_group_message_out_contract() -> None:
    message = _message()

    payload = group_message_payload(message, sender_name="Morty")

    assert payload == {
        "id": str(message.id),
        "role": "assistant",
        "content": "done",
        "participant_id": str(message.participant_id),
        "sender_name": "Morty",
        "mentions": message.mentions,
        "created_at": NOW.isoformat(),
        "cursor": f"{NOW.isoformat()}|{message.id}",
    }


@pytest.mark.asyncio
async def test_group_publish_uses_namespaced_connection_and_canonical_event(monkeypatch) -> None:
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message = group_message_payload(_message(), sender_name="Morty")
    send = AsyncMock()
    monkeypatch.setattr("app.api.websocket.manager.send_message", send)

    assert await publish_group_message_created(
        group_id=group_id,
        session_id=session_id,
        message=message,
    )

    send.assert_awaited_once_with(
        group_connection_key(group_id),
        {
            "type": "message.created",
            "group_id": str(group_id),
            "session_id": str(session_id),
            "message": message,
        },
    )


@pytest.mark.asyncio
async def test_group_websocket_requires_active_membership(monkeypatch) -> None:
    websocket = _WebSocket()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: {"sub": str(user_id)})
    monkeypatch.setattr(group_websocket, "_active_group_user", AsyncMock(return_value=False))
    connect = AsyncMock()
    monkeypatch.setattr(group_websocket.manager, "connect", connect)

    await group_websocket.websocket_group(websocket, group_id, token="token")  # type: ignore[arg-type]

    assert websocket.accepted
    assert websocket.closed_with == 4003
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_websocket_rejects_invalid_token_before_membership_lookup(monkeypatch) -> None:
    websocket = _WebSocket()
    membership = AsyncMock()
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(group_websocket, "_active_group_user", membership)

    await group_websocket.websocket_group(websocket, uuid.uuid4(), token="bad")  # type: ignore[arg-type]

    assert websocket.closed_with == 4001
    membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_websocket_registers_one_group_scoped_connection(monkeypatch) -> None:
    websocket = _WebSocket()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: {"sub": str(user_id)})
    monkeypatch.setattr(group_websocket, "_active_group_user", AsyncMock(return_value=True))
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(group_websocket.manager, "connect", connect)
    monkeypatch.setattr(group_websocket.manager, "disconnect", disconnect)

    await group_websocket.websocket_group(websocket, group_id, token="token")  # type: ignore[arg-type]

    key = group_connection_key(group_id)
    connect.assert_awaited_once_with(key, websocket, user_id=str(user_id))
    disconnect.assert_awaited_once_with(key, websocket)
    assert websocket.sent == [{"type": "connected", "group_id": str(group_id)}]


@pytest.mark.asyncio
async def test_group_websocket_closes_when_membership_is_removed(monkeypatch) -> None:
    websocket = _TimeoutWebSocket()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    monkeypatch.setattr(group_websocket, "decode_access_token", lambda _token: {"sub": str(user_id)})
    membership = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(group_websocket, "_active_group_user", membership)
    monkeypatch.setattr(group_websocket.manager, "connect", AsyncMock())
    disconnect = AsyncMock()
    monkeypatch.setattr(group_websocket.manager, "disconnect", disconnect)

    await group_websocket.websocket_group(websocket, group_id, token="token")  # type: ignore[arg-type]

    assert membership.await_count == 2
    assert websocket.closed_with == 4003
    disconnect.assert_awaited_once()
