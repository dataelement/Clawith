from app.services.channel_user_service import ChannelUserService
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
