from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.channel_user_service import ChannelUserService
from app.services.channel_user_service import ChannelUserResolutionError
from app.services.sso_service import sso_service


def test_sso_identity_lookup_chain_prioritizes_unionid_then_userid_then_openid():
    lookup_chain = sso_service._identity_lookup_chain(
        "feishu",
        "ou_open_123",
        {
            "raw_data": {
                "open_id": "ou_open_123",
                "union_id": "on_union_456",
                "user_id": "u_emp_789",
            }
        },
    )

    assert lookup_chain == [
        ("unionid", "on_union_456"),
        ("external_id", "u_emp_789"),
        ("open_id", "ou_open_123"),
    ]


def test_sso_extract_identity_ids_uses_real_union_id_not_open_id():
    union_id, open_id, external_id = sso_service._extract_identity_ids(
        "feishu",
        "ou_open_123",
        {
            "raw_data": {
                "open_id": "ou_open_123",
                "union_id": "on_union_456",
                "user_id": "u_emp_789",
            }
        },
    )

    assert union_id == "on_union_456"
    assert open_id == "ou_open_123"
    assert external_id == "u_emp_789"


def test_sso_extract_identity_ids_handles_registration_wrapped_payload():
    union_id, open_id, external_id = sso_service._extract_identity_ids(
        "dingtalk",
        "open_123",
        {
            "name": "Alice",
            "raw_data": {
                "openId": "open_123",
                "unionId": "union_456",
            },
        },
    )

    assert union_id == "union_456"
    assert open_id == "open_123"
    assert external_id is None


def test_channel_user_service_keeps_feishu_user_id_out_of_unionid():
    service = ChannelUserService()

    union_id, open_id, external_id = service._get_channel_ids(
        "feishu",
        "ou_open_123",
        {
            "external_id": "u_emp_789",
            "unionid": "on_union_456",
            "open_id": "ou_open_123",
        },
    )

    assert union_id == "on_union_456"
    assert open_id == "ou_open_123"
    assert external_id == "u_emp_789"


def test_channel_user_service_maps_generic_channels_to_dedicated_provider():
    service = ChannelUserService()

    assert service._normalize_channel_type("wechat") == "wechat"
    assert service._normalize_channel_type("slack") == "slack"
    assert service._normalize_channel_type("teams") == "teams"
    assert service._normalize_channel_type("microsoft_teams") == "teams"
    assert service._normalize_channel_type("feishu") == "feishu"


def test_channel_user_service_keeps_generic_channel_external_ids_unscoped():
    service = ChannelUserService()

    assert service._get_channel_ids("wechat", "wx_user_123", {}) == (None, None, "wx_user_123")
    assert service._get_channel_ids("slack", "U123456", {}) == (None, None, "U123456")
    assert service._get_channel_ids("teams", "29:abc", {}) == (None, None, "29:abc")


@pytest.mark.asyncio
async def test_channel_user_service_uses_feishu_open_id_for_existing_member_lookup():
    service = ChannelUserService()
    db = AsyncMock()
    expected_member = SimpleNamespace(id="member-1")
    db.execute = AsyncMock(
        return_value=Mock(scalar_one_or_none=Mock(return_value=expected_member))
    )

    member = await service._find_org_member(
        db,
        provider_id="provider-1",
        channel_type="feishu",
        external_user_id=None,
        extra_info={"open_id": "ou_open_123"},
    )

    assert member is expected_member
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_user_service_rejects_feishu_open_id_only_lazy_registration():
    service = ChannelUserService()
    db = AsyncMock()
    db.get.return_value = None
    agent = SimpleNamespace(tenant_id="tenant-1")

    service._ensure_provider = AsyncMock(return_value=SimpleNamespace(id="provider-1"))
    service._find_org_member = AsyncMock(return_value=None)

    with pytest.raises(ChannelUserResolutionError):
        await service.resolve_channel_user(
            db=db,
            agent=agent,
            channel_type="feishu",
            external_user_id=None,
            extra_info={"open_id": "ou_open_123"},
        )


@pytest.mark.asyncio
async def test_channel_user_service_skips_dingtalk_lookup_when_ids_missing():
    service = ChannelUserService()
    db = AsyncMock()

    member = await service._find_org_member(
        db,
        provider_id="provider-1",
        channel_type="dingtalk",
        external_user_id=None,
        extra_info={},
    )

    assert member is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_user_service_uses_wechat_external_id_for_existing_member_lookup():
    service = ChannelUserService()
    db = AsyncMock()
    expected_member = SimpleNamespace(id="member-wechat-1")
    db.execute = AsyncMock(
        return_value=Mock(scalar_one_or_none=Mock(return_value=expected_member))
    )

    member = await service._find_org_member(
        db,
        provider_id="provider-1",
        channel_type="wechat",
        external_user_id="wx_user_123",
        extra_info={"external_id": "wx_user_123"},
    )

    assert member is expected_member
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_user_service_creates_wechat_org_member_shell_for_lazy_registration():
    service = ChannelUserService()
    db = AsyncMock()
    db.get.return_value = None
    agent = SimpleNamespace(tenant_id="tenant-1")
    provider = SimpleNamespace(id="provider-1")
    created_user = SimpleNamespace(id="user-1")

    service._ensure_provider = AsyncMock(return_value=provider)
    service._find_org_member = AsyncMock(return_value=None)
    service._create_channel_user = AsyncMock(return_value=created_user)
    service._create_org_member_shell = AsyncMock()

    user = await service.resolve_channel_user(
        db=db,
        agent=agent,
        channel_type="wechat",
        external_user_id="wx_user_123",
        extra_info={"external_id": "wx_user_123"},
    )

    assert user is created_user
    service._create_org_member_shell.assert_awaited_once_with(
        db,
        provider,
        "wechat",
        "wx_user_123",
        {"external_id": "wx_user_123"},
        linked_user_id="user-1",
    )
