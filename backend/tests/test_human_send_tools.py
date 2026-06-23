import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import agent_tools


def _tool_schema(tool_name):
    return next(
        tool["function"]["parameters"]
        for tool in agent_tools.AGENT_TOOLS
        if tool["function"]["name"] == tool_name
    )


def _make_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "access_mode": "company",
        "status": "running",
        "is_expired": False,
        "expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_member(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": None,
        "name": "张三",
        "status": "active",
        "provider_id": None,
        "external_id": None,
        "open_id": None,
        "unionid": None,
        "synced_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_provider(**overrides):
    values = {
        "id": uuid.uuid4(),
        "provider_type": "dingtalk",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "display_name": "张三",
        "username": "zhangsan",
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.added = []
        self.committed = False

    async def execute(self, _statement, _params=None):
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_send_channel_message_uses_target_member_id_and_dispatches_channel():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    provider = _make_provider(provider_type="dingtalk")
    member = _make_member(tenant_id=tenant_id, provider_id=provider.id, external_id="dt_1")
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, provider)]),
    ])

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._send_dingtalk_message", new_callable=AsyncMock) as mock_send,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_send.return_value = "sent"

        result = await agent_tools._send_channel_message(
            source.id,
            {"target_member_id": str(member.id), "channel": "dingtalk", "message": "hi"},
        )

    assert result == "sent"
    mock_send.assert_awaited_once_with(source.id, member.name, "hi", member)


@pytest.mark.asyncio
async def test_send_feishu_message_legacy_entry_delegates_to_channel_message():
    agent_id = uuid.uuid4()

    with patch("app.services.agent_tools._send_channel_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = "sent"

        result = await agent_tools._send_feishu_message(
            agent_id,
            {"user_id": "ou_1", "message": "hi"},
        )

    assert result == "sent"
    mock_send.assert_awaited_once()
    delegated_args = mock_send.await_args.args[1]
    assert delegated_args["provider_user_id"] == "ou_1"
    assert delegated_args["channel"] == "feishu"


@pytest.mark.asyncio
async def test_send_platform_message_uses_target_member_id():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    user = _make_user(tenant_id=tenant_id)
    member = _make_member(tenant_id=tenant_id, user_id=user.id)
    session = SimpleNamespace(id=uuid.uuid4(), last_message_at=None)
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, None)]),
        DummyResult(scalar_value=user),
    ])

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.chat_session_service.ensure_primary_platform_session", new_callable=AsyncMock) as mock_session,
        patch("app.api.websocket.maybe_mark_session_read_for_active_viewer", new_callable=AsyncMock),
        patch("app.api.websocket.manager") as mock_manager,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = session
        mock_manager.send_to_user = AsyncMock()

        result = await agent_tools._send_platform_message(
            source.id,
            {"target_member_id": str(member.id), "message": "hi"},
        )

    assert result.startswith("✅")
    mock_session.assert_awaited_once_with(db, source.id, user.id)
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].user_id == user.id


def test_human_send_tool_schemas_are_id_first():
    platform_schema = _tool_schema("send_platform_message")
    channel_schema = _tool_schema("send_channel_message")
    feishu_schema = _tool_schema("send_feishu_message")

    assert "target_member_id" in platform_schema["properties"]
    assert "platform_user_id" in platform_schema["properties"]
    assert platform_schema["required"] == ["message"]

    assert "target_member_id" in channel_schema["properties"]
    assert "provider_user_id" in channel_schema["properties"]
    assert channel_schema["required"] == ["message"]
    assert "teams" in channel_schema["properties"]["channel"]["enum"]

    assert "target_member_id" not in feishu_schema["properties"]


@pytest.mark.asyncio
async def test_send_platform_message_keeps_username_fallback():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    user = _make_user(tenant_id=tenant_id, display_name="张三", username="zhangsan")
    member = _make_member(tenant_id=tenant_id, user_id=user.id, name="张三")
    session = SimpleNamespace(id=uuid.uuid4(), last_message_at=None)
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(scalar_value=user),
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, None)]),
        DummyResult(scalar_value=user),
    ])

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.chat_session_service.ensure_primary_platform_session", new_callable=AsyncMock) as mock_session,
        patch("app.api.websocket.maybe_mark_session_read_for_active_viewer", new_callable=AsyncMock),
        patch("app.api.websocket.manager") as mock_manager,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = session
        mock_manager.send_to_user = AsyncMock()

        result = await agent_tools._send_platform_message(
            source.id,
            {"username": "zhangsan", "message": "hi"},
        )

    assert result.startswith("✅")
    mock_session.assert_awaited_once_with(db, source.id, user.id)
    assert db.committed is True
