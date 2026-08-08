"""WeChat channel ingress must attach to the durable Agent Runtime."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from app.api import feishu as feishu_api
from app.services import wechat_channel


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, agent: object) -> None:
        self.agent = agent
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(self.agent)

    async def commit(self) -> None:
        self.commits += 1


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self):
        return self.session


@pytest.mark.asyncio
async def test_wechat_message_uses_runtime_delivery_without_legacy_llm_loop(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    user = SimpleNamespace(id=user_id)
    chat_session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent)
    session_factory = _SessionFactory(db)
    intake = SimpleNamespace()
    calls: dict[str, object] = {}

    async def resolve_channel_user(**kwargs):
        calls["resolved_user"] = kwargs
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return chat_session

    async def remember_context(*args, **kwargs):
        calls["context"] = (args, kwargs)

    async def load_agent_and_model(_db, requested_agent_id):
        assert requested_agent_id == agent_id
        return agent, model, None

    async def enqueue_runtime(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(wechat_channel, "async_session", session_factory)
    monkeypatch.setattr(
        wechat_channel.channel_user_service,
        "resolve_channel_user",
        resolve_channel_user,
    )
    monkeypatch.setattr(wechat_channel, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(wechat_channel, "remember_wechat_context", remember_context)
    monkeypatch.setattr(feishu_api, "_load_agent_and_model", load_agent_and_model)
    monkeypatch.setattr(wechat_channel, "enqueue_channel_chat_runtime", enqueue_runtime)

    await wechat_channel._process_wechat_message(
        agent_id,
        {
            "from_user_id": "wechat-user-1",
            "message_id": "provider-message-1",
            "session_id": "provider-session-1",
            "context_token": "context-1",
            "item_list": [{"type": 1, "text_item": {"text": "Hello Runtime"}}],
        },
        SimpleNamespace(
            app_id="wechat-bot",
            extra_config={
                "bot_token": "token-1",
                "baseurl": "https://wechat.example",
                "route_tag": "route-1",
            },
        ),  # type: ignore[arg-type]
    )

    assert db.commits == 1
    assert calls["session"]["created_by_user_id"] == user_id  # type: ignore[index]
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["agent"] is agent
    assert intake_call["user"] is user
    assert intake_call["session"] is chat_session
    assert intake_call["model"] is model
    assert intake_call["content"] == "Hello Runtime"
    assert intake_call["source_channel"] == "wechat"
    assert intake_call["channel_delivery_target"] == {"user_id": "wechat-user-1"}
    assert intake_call["message_id"] == wechat_channel.channel_message_id(
        agent_id,
        "wechat",
        "provider-message-1",
    )
