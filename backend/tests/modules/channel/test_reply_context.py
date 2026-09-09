from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.errors import InvalidInput
from app.modules.channel.reply_context import ChannelContextCodec, ContextScope, ReplyContext

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def scope():
    return ContextScope(uuid4(), uuid4(), uuid4(), uuid4(), "event", NOW + timedelta(minutes=15))


def test_roundtrip_rotation_expiry_and_secret_redaction():
    old = ChannelContextCodec(active_key_version="old", keys={"old": b"a" * 32})
    new = ChannelContextCodec(active_key_version="new", keys={"old": b"a" * 32, "new": b"b" * 32})
    context = ReplyContext(provider="discord", conversation_id="123", reply_token=SecretStr("secret-value"))
    binding = scope()
    sealed = old.seal(context, scope=binding)
    assert new.open(sealed, scope=binding, now=NOW) == context
    assert b"secret-value" not in sealed.ciphertext
    assert "secret-value" not in repr(context)
    assert new.seal(context, scope=binding).key_version == "new"
    with pytest.raises(InvalidInput, match="expired"):
        new.open(sealed, scope=binding, now=binding.expires_at)


@pytest.mark.parametrize("field", ["id", "tenant_id", "agent_id", "channel_configuration_id", "external_event_id", "expires_at"])
def test_ciphertext_is_bound_to_exact_owner_event_and_expiry(field):
    codec = ChannelContextCodec(active_key_version="a", keys={"a": b"a" * 32})
    binding = scope()
    sealed = codec.seal(ReplyContext(provider="wechat", conversation_id="chat", reply_token=SecretStr("token")), scope=binding)
    replacement = "other-event" if field == "external_event_id" else NOW + timedelta(hours=1) if field == "expires_at" else uuid4()
    with pytest.raises(InvalidInput):
        codec.open(sealed, scope=replace(binding, **{field: replacement}), now=NOW)


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"extra": "secret"}, {"reply_token": None},
    {"reply_token": "x" * 16385}, {"service_url": "https://example.org"},
])
def test_closed_provider_context_rejects_invalid_shape(changes):
    data = {"provider": "discord", "conversation_id": "123", "reply_token": "token"} | changes
    with pytest.raises(ValidationError):
        ReplyContext.model_validate(data)


def test_teams_requires_https_signed_coordinate_without_token():
    context = ReplyContext(provider="teams", conversation_id="chat", service_url="https://smba.trafficmanager.net/emea/")
    codec = ChannelContextCodec(active_key_version="a", keys={"a": b"a" * 32})
    binding = scope()
    assert codec.open(codec.seal(context, scope=binding), scope=binding, now=NOW) == context
    for url in ("http://example.org", "https://user:password@example.org", "https://example.org?token=secret"):
        with pytest.raises(ValidationError):
            ReplyContext(provider="teams", conversation_id="chat", service_url=url)
