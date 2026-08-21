import uuid
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch

from app.services.feishu_group_targets import (
    FeishuGroupTargetError,
    format_feishu_group_target,
    resolve_feishu_group_target,
    sync_feishu_group_targets,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, _statement):
        return _Result(self.values.pop(0))

    async def flush(self):
        return None

    async def commit(self):
        return None


def _session(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
        "group_name": "项目群",
        "title": "Feishu Group",
        "external_conv_id": "feishu_group_oc_group_1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_group_directory_payload_exposes_stable_target_without_provider_id():
    payload = format_feishu_group_target(_session())

    assert payload["member_type"] == "group"
    assert payload["target_recipient_id"]
    assert payload["contact_tools"] == ["send_channel_message"]
    assert "external_conv_id" not in payload
    assert "chat_id" not in payload


@pytest.mark.asyncio
async def test_resolve_group_target_returns_frozen_delivery_route():
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session = _session(tenant_id=tenant_id, agent_id=agent_id)
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)

    target = await resolve_feishu_group_target(
        _DB(agent, session),
        agent_id=agent_id,
        target_recipient_id=session.id,
    )

    assert target.chat_id == "oc_group_1"
    assert target.delivery_target() == {
        "kind": "session",
        "session_id": str(session.id),
        "channel_delivery": {
            "version": 1,
            "channel": "feishu",
            "target": {"receive_id": "oc_group_1", "receive_id_type": "chat_id"},
        },
    }


@pytest.mark.asyncio
async def test_resolve_group_target_rejects_unavailable_or_cross_scope_target():
    agent_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=uuid.uuid4())

    with pytest.raises(FeishuGroupTargetError) as exc:
        await resolve_feishu_group_target(
            _DB(agent, None),
            agent_id=agent_id,
            target_recipient_id=uuid.uuid4(),
        )

    assert exc.value.code == "feishu_group_target_not_found"


@pytest.mark.asyncio
async def test_sync_group_targets_discovers_bot_groups_without_inbound_message():
    agent = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), creator_id=uuid.uuid4())
    config = SimpleNamespace(app_id="app", app_secret="secret")
    session = _session(agent_id=agent.id, tenant_id=agent.tenant_id, group_name="old")
    response = {
        "code": 0,
        "data": {
            "items": [{"chat_id": "oc_group_1", "name": "项目群", "chat_mode": "group"}],
            "has_more": False,
        },
    }

    with (
        patch(
            "app.services.feishu_group_targets.feishu_service.list_bot_chats",
            new=AsyncMock(return_value=response),
        ) as list_chats,
        patch(
            "app.services.feishu_group_targets.find_or_create_channel_session",
            new=AsyncMock(return_value=session),
        ) as find_session,
    ):
        count = await sync_feishu_group_targets(_DB(config), agent=agent)

    assert count == 1
    list_chats.assert_awaited_once_with(
        "app",
        "secret",
        page_size=100,
        page_token=None,
    )
    assert find_session.await_args.kwargs["external_conv_id"] == "feishu_group_oc_group_1"
    assert session.group_name == "项目群"
