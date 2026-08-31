import asyncio
from types import SimpleNamespace

import pytest

from app.services.realtime_runtime import router as realtime_router_module
from app.services.realtime_runtime.router import RealtimeRouter


class DummyWebSocket:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_realtime_router_falls_back_to_local_connections_when_redis_unavailable(monkeypatch):
    async def unavailable_redis():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(realtime_router_module, "get_redis", unavailable_redis)

    router = RealtimeRouter()
    websocket = DummyWebSocket()

    connection_id = await router.register_connection(
        agent_id="agent-1",
        websocket=websocket,
        session_id="session-1",
        user_id="user-1",
    )

    assert connection_id
    assert websocket.state.realtime_connection_id == connection_id

    await router.route_message(
        agent_id="agent-1",
        message={"type": "chunk", "content": "hello"},
        local_connections=[(websocket, "session-1", "user-1")],
        session_id="session-1",
        user_id="user-1",
    )

    assert websocket.sent == [{"type": "chunk", "content": "hello"}]

    await router.unregister_connection(agent_id="agent-1", websocket=websocket)


@pytest.mark.asyncio
async def test_realtime_subscriber_retries_after_initial_redis_failure(monkeypatch):
    subscribed = asyncio.Event()
    calls = 0

    class FakePubSub:
        async def subscribe(self, _channel: str) -> None:
            subscribed.set()

        async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float):
            await asyncio.sleep(0.01)
            return None

        async def unsubscribe(self, _channel: str) -> None:
            pass

        async def aclose(self) -> None:
            pass

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

    async def flaky_redis():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("redis is down")
        return FakeRedis()

    monkeypatch.setattr(realtime_router_module, "get_redis", flaky_redis)
    monkeypatch.setattr(realtime_router_module, "SUBSCRIBER_RETRY_SECONDS", 0)

    router = RealtimeRouter()
    task = asyncio.create_task(router._subscriber_loop(lambda **_kwargs: None))
    await asyncio.wait_for(subscribed.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 2


@pytest.mark.asyncio
async def test_connection_manager_viewing_session_uses_local_connections_without_redis(monkeypatch):
    from app.api import websocket as websocket_api

    manager = websocket_api.ConnectionManager()
    manager.active_connections["agent-1"] = [(DummyWebSocket(), "session-1", "user-1")]

    async def redis_should_not_be_checked(*_args, **_kwargs):
        raise AssertionError("local viewer should be detected before Redis presence lookup")

    monkeypatch.setattr(websocket_api.realtime_router, "is_user_viewing_session", redis_should_not_be_checked)

    assert await manager.is_user_viewing_session("agent-1", "session-1", "user-1") is True
