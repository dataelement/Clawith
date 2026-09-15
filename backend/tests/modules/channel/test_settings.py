import json

import pytest

from app.infrastructure.errors import InvalidInput
from app.modules.channel.settings import validate_settings


@pytest.mark.parametrize("provider,settings", [
    ("slack",{}),
    ("discord",{"connection_mode":"gateway"}),
    ("teams",{"tenant_id":"botframework.com"}),
    ("feishu",{"connection_mode":"webhook","bot_open_id":"bot","tenant_key":"tenant"}),
    ("wecom",{"connection_mode":"websocket"}),
    ("dingtalk",{"connection_mode":"stream","robot_code":"robot"}),
    ("wechat",{"connection_mode":"long_poll","base_url":"https://ilinkai.weixin.qq.com","channel_version":"1"}),
])
def test_provider_configuration_is_closed_and_has_no_secret_slot(provider, settings):
    assert json.loads(validate_settings(provider, json.dumps(settings))).items() >= settings.items()
    with pytest.raises(InvalidInput):
        validate_settings(provider, json.dumps(settings | {"secret":"must-not-be-config"}))


@pytest.mark.parametrize("provider,settings", [
    ("discord",{"connection_mode":"webhook"}),
    ("wecom",{"connection_mode":"webhook"}),
    ("teams",{"tenant_id":"../other"}),
    ("wechat",{"connection_mode":"long_poll","base_url":"https://user:secret@example.org","channel_version":"1"}),
])
def test_incomplete_or_unsafe_provider_configuration_is_rejected(provider, settings):
    with pytest.raises(InvalidInput):
        validate_settings(provider, json.dumps(settings))


def test_settings_utf8_errors_are_sanitized():
    with pytest.raises(InvalidInput):
        validate_settings("slack", "\ud800")
