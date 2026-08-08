"""HTTP channel webhooks must submit messages to the durable Runtime."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace
import uuid

import pytest

from app import database
from app.api import feishu as feishu_api
from app.api import dingtalk, discord_bot, slack, teams, wecom, whatsapp
from app.services import channel_session
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake
from app.services.agent_runtime.contracts import RunHandle, RuntimeEventCursor
from app.services.channel_user_service import channel_user_service


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = iter(results)
        self.commits = 0
        self.flushes = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(next(self.results))

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def close(self) -> None:
        self.closed = True


class _Request:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return self._body

    async def json(self) -> dict:
        return json.loads(self._body)


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self):
        return self.session


def _runtime(tenant_id: uuid.UUID):
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
        created=True,
    )
    return (
        ChatRuntimeIntake(
            handle=handle,
            message_id=uuid.uuid4(),
            resumed=False,
            stream_after=cursor,
        ),
        cursor,
    )


@pytest.mark.asyncio
async def test_slack_webhook_uses_runtime_intake(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    event_id = f"slack-event-{uuid.uuid4()}"
    config = SimpleNamespace(encrypt_key="", app_secret="")
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, creator_id=uuid.uuid4())
    user = SimpleNamespace(id=user_id, display_name="Slack User U123")
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(config, agent)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(slack, "enqueue_channel_chat_runtime", enqueue)

    result = await slack.slack_event_webhook(
        agent_id,
        _Request(
            {
                "type": "event_callback",
                "event_id": event_id,
                "event": {
                    "type": "message",
                    "channel": "D123",
                    "user": "U123",
                    "text": "Hello Slack",
                },
            }
        ),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert result == {"ok": True}
    assert db.commits == 1
    assert db.closed is True
    session_call = calls["session"]
    assert isinstance(session_call, dict)
    assert session_call["created_by_user_id"] == user_id
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "slack"
    assert intake_call["channel_delivery_target"] == {"channel_id": "D123"}
    assert intake_call["message_id"] == slack.channel_message_id(
        agent_id,
        "slack",
        event_id,
    )


@pytest.mark.asyncio
async def test_teams_webhook_uses_runtime_intake(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    activity_id = f"teams-activity-{uuid.uuid4()}"
    config = SimpleNamespace(
        app_id="bot-1",
        app_secret="",
        extra_config={"use_managed_identity": False},
        is_connected=False,
    )
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, creator_id=uuid.uuid4())
    user = SimpleNamespace(id=user_id, display_name="Teams User sender-1")
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(config, agent)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(teams, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(teams, "_load_agent_and_model", load_model)
    monkeypatch.setattr(teams, "enqueue_channel_chat_runtime", enqueue)

    result = await teams.teams_event_webhook(
        agent_id,
        _Request(
            {
                "type": "message",
                "id": activity_id,
                "text": "Hello Teams",
                "from": {"id": "sender-1", "name": "Alice"},
                "recipient": {"id": "bot-1"},
                "conversation": {
                    "id": "teams-conversation-1",
                    "conversationType": "personal",
                },
            }
        ),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert result == {"ok": True}
    assert db.commits == 1
    assert db.closed is True
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "microsoft_teams"
    assert intake_call["channel_delivery_target"] == {
        "conversation_id": "teams-conversation-1",
        "reply_to_id": activity_id,
        "bot_account": {"id": "bot-1"},
        "recipient": {"id": "sender-1", "name": "Alice"},
    }
    assert intake_call["message_id"] == teams.channel_message_id(
        agent_id,
        "microsoft_teams",
        activity_id,
    )


@pytest.mark.asyncio
async def test_whatsapp_webhook_uses_runtime_intake(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    provider_message_id = f"wamid-{uuid.uuid4()}"
    config = SimpleNamespace(encrypt_key="", app_id="phone-number-1", app_secret="token-1")
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    user = SimpleNamespace(id=user_id)
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(config, agent)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(whatsapp, "enqueue_channel_chat_runtime", enqueue)

    result = await whatsapp.whatsapp_event_webhook(
        agent_id,
        _Request(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "contacts": [{"profile": {"name": "Alice"}}],
                                    "messages": [
                                        {
                                            "id": provider_message_id,
                                            "from": "15551234567",
                                            "type": "text",
                                            "text": {"body": "Hello WhatsApp"},
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                ]
            }
        ),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert result == {"ok": True}
    assert db.commits == 1
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "whatsapp"
    assert intake_call["channel_delivery_target"] == {"phone": "15551234567"}
    assert intake_call["message_id"] == whatsapp.channel_message_id(
        agent_id,
        "whatsapp",
        provider_message_id,
    )


@pytest.mark.asyncio
async def test_dingtalk_message_uses_runtime_and_group_scope(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    provider_message_id = f"dingtalk-message-{uuid.uuid4()}"
    agent = SimpleNamespace(
        id=agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Runtime Agent",
    )
    user = SimpleNamespace(id=user_id)
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent, None)
    session_factory = _SessionFactory(db)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
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
    monkeypatch.setattr(dingtalk, "enqueue_channel_chat_runtime", enqueue)

    await dingtalk.process_dingtalk_message(
        agent_id=agent_id,
        sender_staff_id="staff-1",
        user_text="Hello DingTalk",
        conversation_id="group-1",
        conversation_type="2",
        session_webhook="https://dingtalk.example/session",
        message_id=provider_message_id,
    )

    assert db.commits == 1
    session_call = calls["session"]
    assert isinstance(session_call, dict)
    assert session_call["is_group"] is True
    assert session_call["created_by_user_id"] == user_id
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["source_channel"] == "dingtalk"
    assert intake_call["channel_delivery_target"] == {
        "session_webhook": "https://dingtalk.example/session",
        "user_id": "staff-1",
        "title": "Runtime Agent",
        "source_message_id": provider_message_id,
        "conversation_id": "group-1",
    }
    assert intake_call["message_id"] == dingtalk.channel_message_id(
        agent_id,
        "dingtalk",
        provider_message_id,
    )


@pytest.mark.asyncio
async def test_discord_interaction_commits_runtime_before_deferred_ack(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    interaction_id = f"discord-interaction-{uuid.uuid4()}"
    config = SimpleNamespace(encrypt_key="", app_id="app-1", app_secret="bot-token-1")
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, creator_id=uuid.uuid4())
    user = SimpleNamespace(id=user_id, display_name="Discord User sender-1")
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(config, agent)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(discord_bot, "enqueue_channel_chat_runtime", enqueue)

    result = await discord_bot.discord_interaction_webhook(
        agent_id,
        _Request(
            {
                "id": interaction_id,
                "type": 2,
                "token": "interaction-token-1",
                "channel_id": "channel-1",
                "guild_id": "guild-1",
                "member": {"user": {"id": "sender-1", "username": "Alice"}},
                "data": {
                    "name": "ask",
                    "options": [{"name": "message", "value": "Hello Discord"}],
                },
            }
        ),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert result == {"type": 5}
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["channel_delivery_target"] == {
        "channel_id": "channel-1",
        "interaction_token": "interaction-token-1",
    }
    assert intake_call["message_id"] == discord_bot.channel_message_id(
        agent_id,
        "discord",
        interaction_id,
    )


@pytest.mark.asyncio
async def test_wecom_accepts_runtime_before_async_delivery(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    provider_message_id = f"wecom-message-{uuid.uuid4()}"
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, creator_id=uuid.uuid4())
    user = SimpleNamespace(id=user_id)
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent)
    session_factory = _SessionFactory(db)
    intake, _cursor = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_user(**_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(wecom, "async_session", session_factory)
    monkeypatch.setattr(channel_user_service, "resolve_channel_user", resolve_user)
    monkeypatch.setattr(wecom, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_model)
    monkeypatch.setattr(wecom, "enqueue_channel_chat_runtime", enqueue)

    result = await wecom._accept_wecom_text(
        agent_id=agent_id,
        from_user="wecom-user-1",
        user_text="Hello WeCom",
        chat_id="wecom-group-1",
        external_event_id=provider_message_id,
    )

    assert db.commits == 1
    assert result is None
    session_call = calls["session"]
    assert isinstance(session_call, dict)
    assert session_call["is_group"] is True
    assert session_call["created_by_user_id"] == user_id
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["message_id"] == wecom.channel_message_id(
        agent_id,
        "wecom",
        provider_message_id,
    )
