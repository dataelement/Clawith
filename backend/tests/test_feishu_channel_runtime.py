"""Feishu messages must be accepted by the durable Runtime before acknowledgement."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest

from app.api import feishu
from app.services import channel_session
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake
from app.services.agent_runtime.contracts import RunHandle, RuntimeEventCursor


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, value: object) -> None:
        self.value = value
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(self.value)

    async def commit(self) -> None:
        self.commits += 1


class _SessionFactory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = iter(sessions)

    def __call__(self):
        return next(self.sessions)


def _runtime(tenant_id: uuid.UUID) -> ChatRuntimeIntake:
    run_id = uuid.uuid4()
    return ChatRuntimeIntake(
        handle=RunHandle(
            tenant_id=tenant_id,
            run_id=run_id,
            thread_id=str(run_id),
            command_id=uuid.uuid4(),
            runtime_type="langgraph",
            created=True,
        ),
        message_id=uuid.uuid4(),
        resumed=False,
        stream_after=RuntimeEventCursor(
            created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            event_id=uuid.uuid4(),
        ),
    )


@pytest.mark.asyncio
async def test_feishu_group_message_uses_runtime_intake(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    event_id = f"feishu-event-{uuid.uuid4()}"
    agent = SimpleNamespace(
        id=agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Runtime Agent",
    )
    user = SimpleNamespace(id=user_id, display_name="Alice")
    session = SimpleNamespace(id=session_id)
    model = SimpleNamespace(id=uuid.uuid4())
    config = SimpleNamespace(app_id="app-1", app_secret="secret-1")
    db = _Session(agent)
    intake = _runtime(tenant_id)
    calls: dict[str, object] = {}

    async def resolve_sender(_db, **_kwargs):
        return user

    async def find_session(**kwargs):
        calls["session"] = kwargs
        return session

    async def load_model(_db, _agent_id):
        return agent, model, None

    async def enqueue(_db, **kwargs):
        calls["intake"] = kwargs
        return intake

    monkeypatch.setattr(feishu, "_async_session", _SessionFactory(db))
    monkeypatch.setattr(feishu, "_resolve_feishu_sender", resolve_sender)
    monkeypatch.setattr(channel_session, "find_or_create_channel_session", find_session)
    monkeypatch.setattr(feishu, "_load_agent_and_model", load_model)
    monkeypatch.setattr(feishu, "enqueue_channel_chat_runtime", enqueue)

    result = await feishu._accept_feishu_runtime_message(
        agent_id=agent_id,
        config=config,  # type: ignore[arg-type]
        sender_open_id="ou_sender",
        sender_user_id="feishu-user-1",
        chat_type="group",
        chat_id="oc_group_1",
        content="Hello Feishu",
        display_content="Hello Feishu",
        external_event_id=event_id,
    )

    assert db.commits == 1
    assert result is intake
    session_call = calls["session"]
    assert isinstance(session_call, dict)
    assert session_call["is_group"] is True
    assert session_call["created_by_user_id"] == user_id
    intake_call = calls["intake"]
    assert isinstance(intake_call, dict)
    assert intake_call["content"] == "[发送者: Alice] Hello Feishu"
    assert intake_call["display_content"] == "Hello Feishu"
    assert intake_call["channel_delivery_target"] == {
        "receive_id": "oc_group_1",
        "receive_id_type": "chat_id",
    }
    assert intake_call["message_id"] == feishu.channel_message_id(
        agent_id,
        "feishu",
        event_id,
    )


@pytest.mark.asyncio
async def test_feishu_event_commits_runtime_before_provider_ack(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    event_id = f"feishu-event-{uuid.uuid4()}"
    config = SimpleNamespace(app_id="app-1", app_secret="secret-1")
    intake = _runtime(tenant_id)
    config_db = _Session(config)
    calls: dict[str, object] = {}

    async def accept(**kwargs):
        calls["accept"] = kwargs
        return intake

    feishu._processed_events.discard(event_id)
    monkeypatch.setattr(feishu, "_async_session", _SessionFactory(config_db))
    monkeypatch.setattr(feishu, "_accept_feishu_runtime_message", accept)

    result = await feishu.process_feishu_event(
        agent_id,
        {
            "header": {
                "event_id": event_id,
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": "ou_sender",
                        "user_id": "feishu-user-1",
                    }
                },
                "message": {
                    "message_id": "om_message_1",
                    "message_type": "text",
                    "chat_type": "p2p",
                    "chat_id": "oc_chat_1",
                    "content": '{"text":"Hello Feishu"}',
                },
            },
        },
    )

    assert result == {"code": 0, "msg": "ok"}
    assert event_id in feishu._processed_events
    accepted = calls["accept"]
    assert isinstance(accepted, dict)
    assert accepted["external_event_id"] == event_id


@pytest.mark.asyncio
async def test_feishu_image_keeps_base64_out_of_display_content(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    config = SimpleNamespace(app_id="app-1", app_secret="secret-1")
    calls: dict[str, object] = {}

    async def download(*_args):
        return b"image-bytes"

    async def store(*_args, **_kwargs):
        return "key", "workspace/uploads/image.jpg", SimpleNamespace()

    async def accept(**kwargs):
        calls["accept"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(feishu.feishu_service, "download_message_resource", download)
    monkeypatch.setattr(feishu, "store_agent_upload", store)
    monkeypatch.setattr(feishu, "_accept_feishu_runtime_message", accept)

    result = await feishu._accept_feishu_file_runtime(
        agent_id=agent_id,
        config=config,  # type: ignore[arg-type]
        message={
            "message_id": "om_image_1",
            "message_type": "image",
            "content": '{"image_key":"img_12345678"}',
        },
        sender_open_id="ou_sender",
        sender_user_id="feishu-user-1",
        chat_type="p2p",
        chat_id="oc_chat_1",
        external_event_id="event-1",
    )

    assert result is not None
    accepted = calls["accept"]
    assert isinstance(accepted, dict)
    assert accepted["display_content"] == "[file:image_12345678.jpg]"
    assert "base64," in accepted["content"]
    assert "base64," not in accepted["display_content"]
