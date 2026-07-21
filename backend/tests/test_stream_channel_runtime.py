"""Long-lived channel consumers must attach messages to the shared Runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest

from app import database
from app.api import feishu as feishu_api
from app.services import (
    channel_session,
    discord_gateway,
    wecom_stream,
)
from app.services.agent_runtime import channel_chat
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake
from app.services.agent_runtime.contracts import RunHandle, RuntimeEventCursor
from app.services.channel_user_service import channel_user_service


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, agent: object) -> None:
        self.agent = agent
        self.commits = 0
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(self.agent)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self):
        return self.session


def _runtime(tenant_id: uuid.UUID, *, resumed: bool = True):
    run_id = uuid.uuid4()
    cursor = RuntimeEventCursor(
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        event_id=uuid.uuid4(),
    )
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=not resumed,
    )
    return (
        ChatRuntimeIntake(
            handle=handle,
            message_id=uuid.uuid4(),
            resumed=resumed,
            stream_after=cursor,
        ),
        cursor,
    )


@pytest.mark.asyncio
async def test_wecom_stream_uses_runtime_for_group_message(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, creator_id=uuid.uuid4())
    user = SimpleNamespace(id=user_id)
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent)
    session_factory = _SessionFactory(db)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(database, "async_session", session_factory)
    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(channel_chat, "enqueue_channel_chat_runtime", enqueue)

    reply = await wecom_stream._process_wecom_stream_message(
        agent_id=agent_id,
        sender_id="wecom-user-1",
        user_text="Hello group",
        chat_id="wecom-group-1",
        chat_type="group",
        external_event_id="wecom-message-1",
    )

    assert reply == ""
    assert db.commits == 1
    session_call = calls["session"]
    assert isinstance(session_call, dict)
    assert session_call["is_group"] is True
    assert session_call["created_by_user_id"] == user_id
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "wecom"
    assert intake_call["channel_delivery_target"] == {
        "user_id": "wecom-user-1",
        "chat_id": "wecom-group-1",
        "transport": "websocket",
    }
    assert intake_call["message_id"] == channel_chat.channel_message_id(
        agent_id,
        "wecom",
        "wecom-message-1",
    )


@pytest.mark.asyncio
async def test_discord_gateway_uses_runtime_delivery(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    user = SimpleNamespace(id=user_id, display_name="Discord User 123")
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent)
    session_factory = _SessionFactory(db)
    intake, _cursor = _runtime(tenant_id, resumed=False)
    calls: dict[str, object] = {}

    async def resolve_user(**kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(discord_gateway, "async_session", session_factory)
    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(channel_chat, "enqueue_channel_chat_runtime", enqueue)

    message = SimpleNamespace(
        id=987654,
        author=SimpleNamespace(id=123, display_name="Alice", name="alice"),
        channel=SimpleNamespace(id=456),
        guild=None,
    )
    reply = await discord_gateway.DiscordGatewayManager()._handle_message(
        agent_id,
        message,
        "Hello Discord",
    )

    assert reply is None
    assert db.commits == 1
    assert db.flushes == 1
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "discord"
    assert intake_call["channel_delivery_target"] == {
        "channel_id": "456",
        "reply_to_message_id": "987654",
    }
    assert intake_call["message_id"] == channel_chat.channel_message_id(
        agent_id,
        "discord",
        "987654",
    )
