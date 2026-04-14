import uuid

from app.services.wecom_stream import (
    _build_wecom_conv_id,
    _extract_wecom_chat_id,
    _extract_wecom_chat_type,
    _extract_wecom_sender_id,
    WeComStreamManager,
)


def test_extract_wecom_context_from_official_sdk_shape():
    body = {
        "msgid": "msg_123",
        "msgtype": "text",
        "from_userid": "zhangsan",
        "chattype": "group",
        "chatid": "chat_001",
        "text": {"content": "hello"},
    }

    assert _extract_wecom_sender_id(body) == "zhangsan"
    assert _extract_wecom_chat_type(body) == "group"
    assert _extract_wecom_chat_id(body) == "chat_001"
    assert _build_wecom_conv_id("zhangsan", "chat_001", "group") == "wecom_group_chat_001"


def test_extract_wecom_context_from_nested_legacy_shape():
    body = {
        "from": {"userid": "lisi"},
        "chat_type": "single",
        "chatid": "lisi",
        "text": {"content": "hi"},
    }

    assert _extract_wecom_sender_id(body) == "lisi"
    assert _extract_wecom_chat_type(body) == "single"
    assert _extract_wecom_chat_id(body) == "lisi"
    assert _build_wecom_conv_id("lisi", "lisi", "single") == "wecom_p2p_lisi"


def test_build_wecom_conv_id_falls_back_to_sender_for_missing_group_chat_id():
    assert _build_wecom_conv_id("wangwu", "", "group") == "wecom_p2p_wangwu"


def test_status_uses_connection_state_not_task_liveness():
    agent_id = uuid.uuid4()
    manager = WeComStreamManager()

    manager._connected[agent_id] = False

    assert manager.status() == {str(agent_id): False}


def test_status_reports_connected_agent():
    agent_id = uuid.uuid4()
    manager = WeComStreamManager()

    manager._connected[agent_id] = True

    assert manager.status() == {str(agent_id): True}
