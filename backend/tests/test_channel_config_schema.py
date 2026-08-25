import uuid
from datetime import UTC, datetime

from app.models.channel_config import ChannelConfig
from app.schemas.schemas import ChannelConfigOut


def test_channel_config_response_excludes_credentials_in_all_channel_endpoints() -> None:
    """The shared output schema must not serialize stored channel credentials."""
    config = ChannelConfig(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        channel_type="slack",
        app_id="app-id",
        app_secret="bot-token",
        encrypt_key="signing-secret",
        verification_token="verification-token",
        is_configured=True,
        is_connected=True,
        extra_config={
            "connection_mode": "websocket",
            "bot_id": "bot-id",
            "bot_secret": "bot-secret",
            "nested": {"access_token": "access-token", "safe_setting": "safe"},
        },
        created_at=datetime.now(UTC),
    )

    payload = ChannelConfigOut.model_validate(config).model_dump()

    serialized = str(payload)
    assert "app_secret" not in payload
    assert "encrypt_key" not in payload
    assert "verification_token" not in payload
    assert "bot-token" not in serialized
    assert "signing-secret" not in serialized
    assert "verification-token" not in serialized
    assert "bot-secret" not in serialized
    assert "access-token" not in serialized
    assert payload["extra_config"] == {
        "connection_mode": "websocket",
        "bot_id": "bot-id",
        "nested": {"safe_setting": "safe"},
    }
