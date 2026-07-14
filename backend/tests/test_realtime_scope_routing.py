"""Scoped Redis routing keeps the legacy Agent surface and adds group fan-out."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.api.websocket import ConnectionManager
from app.services import group_realtime
from app.services.group_realtime import (
    GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
    GROUP_MEMBERSHIP_REVOKED_EVENT,
)
from app.services.realtime_runtime.router import RealtimeRouter


class _Pipeline:
    def __init__(self, redis: "_Redis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def __getattr__(self, name: str):
        def stage(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return stage

    async def execute(self):
        results = []
        for name, args, kwargs in self.operations:
            results.append(await getattr(self.redis, name)(*args, **kwargs))
        return results


class _Redis:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: list[tuple[str, int]] = []
        self.published: list[tuple[str, str]] = []

    def pipeline(self, *, transaction: bool):
        assert transaction is True
        return _Pipeline(self)

    async def sadd(self, key: str, *values: str):
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key: str, *values: str):
        members = self.sets.setdefault(key, set())
        for value in values:
            members.discard(value)
        return len(values)

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    async def hset(self, key: str, *, mapping: dict[str, str]):
        self.hashes[key] = dict(mapping)
        return len(mapping)

    async def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int):
        self.expirations.append((key, seconds))
        return True

    async def delete(self, key: str):
        self.hashes.pop(key, None)
        return True

    async def publish(self, channel: str, payload: str):
        self.published.append((channel, payload))
        return 1


class _WebSocket:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.sent: list[dict] = []
        self.closed: list[int] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, *, code: int) -> None:
        self.closed.append(code)


def _stub_group_delivery(
    monkeypatch,
    manager: ConnectionManager,
    *active_participant_ids: str,
) -> None:
    active = frozenset(active_participant_ids)

    async def deliver(*, payload: dict, connections: list[tuple], **_kwargs) -> None:
        for websocket, _, _, participant_id, _ in connections:
            if participant_id in active:
                await websocket.send_json(payload)

    monkeypatch.setattr(manager, "_deliver_current_group_connections", deliver)


class _GroupDeliveryResult:
    def __init__(self, *, scalar=None, scalar_values=()) -> None:
        self.scalar = scalar
        self.scalar_values = tuple(scalar_values)

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.scalar_values)


class _GroupDeliveryTransaction:
    def __init__(self, db: "_GroupDeliveryDB") -> None:
        self.db = db

    async def __aenter__(self):
        self.db.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.db.in_transaction = False
        return False


class _GroupDeliveryDB:
    def __init__(self, group_id: uuid.UUID, active_participant_ids) -> None:
        self.group_id = group_id
        self.active_participant_ids = tuple(active_participant_ids)
        self.in_transaction = False
        self.statements = []

    def begin(self):
        return _GroupDeliveryTransaction(self)

    async def execute(self, statement):
        assert self.in_transaction is True
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _GroupDeliveryResult(scalar=self.group_id)
        return _GroupDeliveryResult(scalar_values=self.active_participant_ids)


class _GroupDeliverySession:
    def __init__(self, db: _GroupDeliveryDB) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _LockAwareWebSocket(_WebSocket):
    def __init__(self, db: _GroupDeliveryDB) -> None:
        super().__init__()
        self.db = db

    async def send_json(self, payload: dict) -> None:
        assert self.db.in_transaction is True
        await super().send_json(payload)


class _PubSub:
    def __init__(
        self,
        *,
        message: dict | None = None,
        subscribe_error: Exception | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self.message = message
        self.subscribe_error = subscribe_error
        self.read_error = read_error
        self.channels: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        if self.subscribe_error is not None:
            raise self.subscribe_error

    async def get_message(self, **_kwargs):
        if self.read_error is not None:
            error = self.read_error
            self.read_error = None
            raise error
        if self.message is not None:
            message = self.message
            self.message = None
            return message
        await asyncio.Future()

    async def aclose(self) -> None:
        self.closed = True


class _PubSubRedis:
    def __init__(self, pubsub: _PubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _PubSub:
        return self._pubsub


@pytest.mark.asyncio
async def test_legacy_agent_registration_and_refresh_keep_existing_keys(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        AsyncMock(return_value=redis),
    )
    router = RealtimeRouter(instance_id="api-a")
    websocket = _WebSocket()

    connection_id = await router.register_connection(
        agent_id="agent-1",
        websocket=websocket,  # type: ignore[arg-type]
        session_id="session-1",
        user_id="user-1",
    )

    index_key = "realtime:ws:agent:agent-1"
    connection_key = f"realtime:ws:conn:{connection_id}"
    assert redis.sets[index_key] == {connection_id}
    assert redis.hashes[connection_key]["agent_id"] == "agent-1"
    assert redis.hashes[connection_key]["scope_type"] == "agent"

    redis.sets.clear()
    redis.hashes.clear()
    assert await router.refresh_connection(
        agent_id="agent-1",
        websocket=websocket,  # type: ignore[arg-type]
    )
    assert redis.sets[index_key] == {connection_id}
    assert redis.hashes[connection_key]["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_group_route_sends_local_and_once_per_remote_instance(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        AsyncMock(return_value=redis),
    )
    source = RealtimeRouter(instance_id="api-a")
    remote = RealtimeRouter(instance_id="api-b")
    local_socket = _WebSocket()
    await remote.register_scope_connection(
        scope_type="group",
        scope_id="group-1",
        websocket=_WebSocket(),  # type: ignore[arg-type]
        user_id="user-2",
        participant_id="participant-2",
        tenant_id="tenant-1",
    )
    await remote.register_scope_connection(
        scope_type="group",
        scope_id="group-1",
        websocket=_WebSocket(),  # type: ignore[arg-type]
        user_id="user-3",
        participant_id="participant-3",
        tenant_id="tenant-1",
    )
    event = {"type": "message.created", "session_id": "session-1"}

    await source.route_scope_message(
        scope_type="group",
        scope_id="group-1",
        message=event,
        local_connections=[(local_socket, None, "user-1", "participant-1", "tenant-1")],
        tenant_id="tenant-1",
    )

    assert local_socket.sent == [event]
    assert len(redis.published) == 1
    channel, raw_envelope = redis.published[0]
    assert channel == "realtime:ws:instance:api-b"
    envelope = json.loads(raw_envelope)
    assert envelope["scope_type"] == "group"
    assert envelope["scope_id"] == "group-1"
    assert envelope["tenant_id"] == "tenant-1"
    assert envelope["message"] == event


@pytest.mark.asyncio
async def test_group_route_targets_only_instances_with_revoked_participant(
    monkeypatch,
) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        AsyncMock(return_value=redis),
    )
    source = RealtimeRouter(instance_id="api-a")
    participant_instance = RealtimeRouter(instance_id="api-b")
    other_instance = RealtimeRouter(instance_id="api-c")
    await participant_instance.register_scope_connection(
        scope_type="group",
        scope_id="group-1",
        websocket=_WebSocket(),  # type: ignore[arg-type]
        participant_id="participant-2",
        tenant_id="tenant-1",
    )
    await other_instance.register_scope_connection(
        scope_type="group",
        scope_id="group-1",
        websocket=_WebSocket(),  # type: ignore[arg-type]
        participant_id="participant-3",
        tenant_id="tenant-1",
    )
    event = {
        "type": GROUP_MEMBERSHIP_REVOKED_EVENT,
        "group_id": "group-1",
        "participant_id": "participant-2",
        "code": GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
    }

    await source.route_scope_message(
        scope_type="group",
        scope_id="group-1",
        message=event,
        participant_id="participant-2",
        tenant_id="tenant-1",
    )

    assert len(redis.published) == 1
    channel, raw_envelope = redis.published[0]
    assert channel == "realtime:ws:instance:api-b"
    envelope = json.loads(raw_envelope)
    assert envelope["participant_id"] == "participant-2"
    assert envelope["tenant_id"] == "tenant-1"
    assert envelope["message"] == event


@pytest.mark.asyncio
async def test_connection_manager_scoped_dispatch_does_not_change_direct_storage(monkeypatch) -> None:
    manager = ConnectionManager()
    _stub_group_delivery(monkeypatch, manager, "participant-1")
    direct = _WebSocket()
    group = _WebSocket()
    register = AsyncMock(return_value="connection-id")
    unregister = AsyncMock()
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.register_scope_connection",
        register,
    )
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.unregister_scope_connection",
        unregister,
    )
    await manager.connect("agent-1", direct, "session-1", "user-1")  # type: ignore[arg-type]
    await manager.connect_scope(
        scope_type="group",
        scope_id="group-1",
        websocket=group,  # type: ignore[arg-type]
        user_id="user-1",
        participant_id="participant-1",
        tenant_id="tenant-1",
        auto_refresh=False,
    )
    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id="group-1",
        payload={"type": "message.created"},
        tenant_id="tenant-1",
    )

    assert manager.active_connections == {"agent-1": [(direct, "session-1", "user-1")]}
    assert group.sent == [{"type": "message.created"}]

    await manager.disconnect("agent-1", direct)  # type: ignore[arg-type]
    await manager.disconnect_scope(
        scope_type="group",
        scope_id="group-1",
        websocket=group,  # type: ignore[arg-type]
    )
    assert manager.active_connections == {}
    assert manager._scoped_connections == {}


@pytest.mark.asyncio
async def test_membership_revoke_closes_target_and_blocks_later_group_events(
    monkeypatch,
) -> None:
    manager = ConnectionManager()
    _stub_group_delivery(monkeypatch, manager, "participant-2")
    target = _WebSocket()
    peer = _WebSocket()
    register = AsyncMock(return_value="connection-id")
    unregister = AsyncMock()
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.register_scope_connection",
        register,
    )
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.unregister_scope_connection",
        unregister,
    )
    monkeypatch.setattr(
        manager,
        "_group_membership_is_active",
        AsyncMock(return_value=False),
    )
    for websocket, participant_id in (
        (target, "participant-1"),
        (peer, "participant-2"),
    ):
        await manager.connect_scope(
            scope_type="group",
            scope_id="group-1",
            websocket=websocket,  # type: ignore[arg-type]
            participant_id=participant_id,
            tenant_id="tenant-1",
            auto_refresh=False,
        )

    revoke = {
        "type": GROUP_MEMBERSHIP_REVOKED_EVENT,
        "group_id": "group-1",
        "participant_id": "participant-1",
        "code": GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
        "content": "Group membership was revoked",
    }
    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id="group-1",
        payload=revoke,
        participant_id="participant-1",
        tenant_id="tenant-1",
    )

    assert target.sent == [revoke]
    assert target.closed == [GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE]
    assert peer.sent == []
    assert peer.closed == []
    assert manager._scoped_connections == {
        ("group", "group-1"): [
            (peer, None, None, "participant-2", "tenant-1")
        ]
    }

    message = {"type": "message.created", "message": {"id": "later"}}
    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id="group-1",
        payload=message,
        tenant_id="tenant-1",
    )

    assert target.sent == [revoke]
    assert peer.sent == [message]
    unregister.assert_awaited_once_with(
        scope_type="group",
        scope_id="group-1",
        websocket=target,
    )


@pytest.mark.asyncio
async def test_delayed_revoke_is_ignored_after_membership_reactivation(
    monkeypatch,
) -> None:
    manager = ConnectionManager()
    target = _WebSocket()
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.register_scope_connection",
        AsyncMock(return_value="connection-id"),
    )
    monkeypatch.setattr(
        manager,
        "_group_membership_is_active",
        AsyncMock(return_value=True),
    )
    await manager.connect_scope(
        scope_type="group",
        scope_id="group-1",
        websocket=target,  # type: ignore[arg-type]
        participant_id="participant-1",
        tenant_id="tenant-1",
        auto_refresh=False,
    )
    revoke = {
        "type": GROUP_MEMBERSHIP_REVOKED_EVENT,
        "group_id": "group-1",
        "participant_id": "participant-1",
        "code": GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
    }

    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id="group-1",
        payload=revoke,
        participant_id="participant-1",
        tenant_id="tenant-1",
    )

    assert target.sent == []
    assert target.closed == []
    assert manager._scoped_connections[("group", "group-1")][0][0] is target


@pytest.mark.asyncio
async def test_revoke_closes_only_connections_snapshotted_before_db_recheck(
    monkeypatch,
) -> None:
    manager = ConnectionManager()
    old_socket = _WebSocket()
    new_socket = _WebSocket()
    membership_query_started = asyncio.Event()
    finish_membership_query = asyncio.Event()

    async def membership_is_inactive(**_kwargs) -> bool:
        membership_query_started.set()
        await finish_membership_query.wait()
        return False

    monkeypatch.setattr(
        "app.api.websocket.realtime_router.register_scope_connection",
        AsyncMock(return_value="connection-id"),
    )
    monkeypatch.setattr(
        "app.api.websocket.realtime_router.unregister_scope_connection",
        AsyncMock(),
    )
    monkeypatch.setattr(
        manager,
        "_group_membership_is_active",
        membership_is_inactive,
    )
    await manager.connect_scope(
        scope_type="group",
        scope_id="group-1",
        websocket=old_socket,  # type: ignore[arg-type]
        participant_id="participant-1",
        tenant_id="tenant-1",
        auto_refresh=False,
    )
    revoke = {
        "type": GROUP_MEMBERSHIP_REVOKED_EVENT,
        "group_id": "group-1",
        "participant_id": "participant-1",
        "code": GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
    }
    delivery = asyncio.create_task(
        manager.deliver_pubsub_scope_message(
            scope_type="group",
            scope_id="group-1",
            payload=revoke,
            participant_id="participant-1",
            tenant_id="tenant-1",
        )
    )
    await asyncio.wait_for(membership_query_started.wait(), timeout=1)
    await manager.connect_scope(
        scope_type="group",
        scope_id="group-1",
        websocket=new_socket,  # type: ignore[arg-type]
        participant_id="participant-1",
        tenant_id="tenant-1",
        auto_refresh=False,
    )
    finish_membership_query.set()
    await delivery

    assert old_socket.closed == [GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE]
    assert new_socket.closed == []
    assert manager._scoped_connections == {
        ("group", "group-1"): [
            (new_socket, None, None, "participant-1", "tenant-1")
        ]
    }


@pytest.mark.asyncio
async def test_failed_revoke_publish_does_not_leak_later_group_message(
    monkeypatch,
) -> None:
    manager = ConnectionManager()
    _stub_group_delivery(monkeypatch, manager, "participant-active")
    removed = _WebSocket()
    active = _WebSocket()
    monkeypatch.setattr(
        group_realtime.realtime_router,
        "route_scope_message",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )
    for websocket, participant_id in (
        (removed, "participant-removed"),
        (active, "participant-active"),
    ):
        manager._add_local_connection(
            scope_type="group",
            scope_id="group-1",
            websocket=websocket,  # type: ignore[arg-type]
            session_id=None,
            user_id=None,
            participant_id=participant_id,
            tenant_id="tenant-1",
        )

    await group_realtime.publish_group_membership_revoked(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        group_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        participant_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
    )
    message = {"type": "message.created", "message": {"id": "later"}}
    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id="group-1",
        payload=message,
        tenant_id="tenant-1",
        participant_allowlist=["participant-active"],
    )

    assert removed.sent == []
    assert active.sent == [message]


@pytest.mark.asyncio
async def test_group_allowlist_filters_local_and_cross_instance_delivery(
    monkeypatch,
) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        AsyncMock(return_value=redis),
    )
    source = RealtimeRouter(instance_id="api-a")
    remote_mixed = RealtimeRouter(instance_id="api-b")
    remote_removed_only = RealtimeRouter(instance_id="api-c")
    for router, participant_id in (
        (remote_mixed, "participant-active"),
        (remote_mixed, "participant-removed"),
        (remote_removed_only, "participant-removed"),
    ):
        await router.register_scope_connection(
            scope_type="group",
            scope_id="group-1",
            websocket=_WebSocket(),  # type: ignore[arg-type]
            participant_id=participant_id,
            tenant_id="tenant-1",
        )
    local_removed = _WebSocket()
    local_active = _WebSocket()
    message = {"type": "message.created", "message": {"id": "message-1"}}

    await source.route_scope_message(
        scope_type="group",
        scope_id="group-1",
        message=message,
        local_connections=[
            (local_removed, None, None, "participant-removed", "tenant-1"),
            (local_active, None, None, "participant-active", "tenant-1"),
        ],
        tenant_id="tenant-1",
        participant_allowlist=["participant-active"],
    )

    assert local_removed.sent == []
    assert local_active.sent == [message]
    assert len(redis.published) == 1
    channel, raw_envelope = redis.published[0]
    assert channel == "realtime:ws:instance:api-b"
    envelope = json.loads(raw_envelope)
    assert envelope["participant_allowlist"] == ["participant-active"]

    remote_manager = ConnectionManager()
    _stub_group_delivery(monkeypatch, remote_manager, "participant-active")
    remote_removed = _WebSocket()
    remote_active = _WebSocket()
    for websocket, participant_id in (
        (remote_removed, "participant-removed"),
        (remote_active, "participant-active"),
    ):
        remote_manager._add_local_connection(
            scope_type="group",
            scope_id="group-1",
            websocket=websocket,  # type: ignore[arg-type]
            session_id=None,
            user_id=None,
            participant_id=participant_id,
            tenant_id="tenant-1",
        )
    await remote_manager.deliver_pubsub_scope_message(
        scope_type=envelope["scope_type"],
        scope_id=envelope["scope_id"],
        payload=envelope["message"],
        tenant_id=envelope["tenant_id"],
        participant_allowlist=envelope["participant_allowlist"],
    )

    assert remote_removed.sent == []
    assert remote_active.sent == [message]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale_remote_allowlist",
    [False, True],
    ids=["ordinary-message", "delayed-remote-envelope"],
)
async def test_group_message_delivery_rechecks_membership_under_group_lock(
    monkeypatch,
    stale_remote_allowlist: bool,
) -> None:
    group_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    active_participant_id = uuid.uuid4()
    removed_participant_id = uuid.uuid4()
    db = _GroupDeliveryDB(group_id, (active_participant_id,))
    monkeypatch.setattr(
        "app.api.websocket.async_session",
        lambda: _GroupDeliverySession(db),
    )
    manager = ConnectionManager()
    active_socket = _LockAwareWebSocket(db)
    removed_socket = _LockAwareWebSocket(db)
    for websocket, participant_id in (
        (active_socket, active_participant_id),
        (removed_socket, removed_participant_id),
    ):
        manager._add_local_connection(
            scope_type="group",
            scope_id=str(group_id),
            websocket=websocket,  # type: ignore[arg-type]
            session_id=None,
            user_id=None,
            participant_id=str(participant_id),
            tenant_id=str(tenant_id),
        )
    message = {"type": "message.created", "message": {"id": str(uuid.uuid4())}}
    participant_allowlist = [str(active_participant_id)]
    if stale_remote_allowlist:
        # Simulate an origin snapshot taken before membership removal. The
        # delayed destination delivery must still use current membership.
        participant_allowlist.append(str(removed_participant_id))

    await manager.deliver_pubsub_scope_message(
        scope_type="group",
        scope_id=str(group_id),
        payload=message,
        tenant_id=str(tenant_id),
        participant_allowlist=participant_allowlist,
    )

    assert active_socket.sent == [message]
    assert removed_socket.sent == []
    assert db.in_transaction is False
    assert len(db.statements) == 2
    assert db.statements[0]._for_update_arg is not None
    assert db.statements[0]._for_update_arg.read is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["get", "subscribe", "read"])
async def test_subscriber_recovers_after_redis_failures(monkeypatch, failure_phase) -> None:
    delivered = asyncio.Event()
    envelope = {
        "type": "message",
        "data": json.dumps(
            {
                "scope_type": "group",
                "scope_id": "group-1",
                "message": {"type": "message.created"},
                "tenant_id": "tenant-1",
            }
        ),
    }
    failing_pubsub = _PubSub(
        subscribe_error=(ConnectionError("subscribe failed") if failure_phase == "subscribe" else None),
        read_error=(ConnectionError("read failed") if failure_phase == "read" else None),
    )
    healthy_pubsub = _PubSub(message=envelope)
    failing_redis = _PubSubRedis(failing_pubsub)
    healthy_redis = _PubSubRedis(healthy_pubsub)
    attempts = 0

    async def fake_get_redis():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if failure_phase == "get":
                raise ConnectionError("get failed")
            return failing_redis
        return healthy_redis

    async def deliver_local(**kwargs) -> None:
        assert kwargs["scope_type"] == "group"
        assert kwargs["scope_id"] == "group-1"
        assert kwargs["tenant_id"] == "tenant-1"
        delivered.set()

    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        fake_get_redis,
    )
    router = RealtimeRouter(instance_id="api-a")
    router._subscriber_retry_initial_seconds = 0.001
    router._subscriber_retry_max_seconds = 0.002

    await router.start(deliver_local, supports_scopes=True)
    try:
        await asyncio.wait_for(delivered.wait(), timeout=1)
    finally:
        await router.stop()

    assert attempts >= 2
    assert router._started is False
    assert router._subscriber_task is None
    assert healthy_pubsub.closed is True
    if failure_phase != "get":
        assert failing_pubsub.closed is True


@pytest.mark.asyncio
async def test_stop_interrupts_subscriber_reconnect_backoff(monkeypatch) -> None:
    attempted = asyncio.Event()

    async def unavailable_redis():
        attempted.set()
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        unavailable_redis,
    )
    router = RealtimeRouter(instance_id="api-a")
    router._subscriber_retry_initial_seconds = 60
    router._subscriber_retry_max_seconds = 60

    await router.start(AsyncMock(), supports_scopes=True)
    await asyncio.wait_for(attempted.wait(), timeout=1)
    await asyncio.wait_for(router.stop(), timeout=0.1)

    assert router._started is False
    assert router._subscriber_task is None
