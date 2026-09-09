"""Provider-owned non-secret configuration; unsupported fields fail explicitly."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.errors import InvalidInput
from app.modules.channel.contracts import Provider


class EmptySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class FeishuSettings(EmptySettings):
    bot_open_id: str = Field(min_length=1, max_length=256)
    tenant_key: str = Field(min_length=1, max_length=256)
    connection_mode: Literal["webhook", "websocket"]


class WeComSettings(EmptySettings):
    corp_id: str | None = Field(default=None, min_length=1, max_length=256)
    agent_id: int | None = Field(default=None, ge=1)
    open_kfid: str | None = Field(default=None, min_length=1, max_length=256)
    connection_mode: Literal["webhook", "websocket", "customer_service"]

    @model_validator(mode="after")
    def require_application_identity(self) -> "WeComSettings":
        if self.connection_mode == "webhook" and (
            self.corp_id is None or self.agent_id is None
        ):
            raise ValueError("Webhook configuration requires application identity")
        if self.connection_mode == "customer_service" and (self.corp_id is None or self.open_kfid is None or self.agent_id is not None):
            raise ValueError("Customer service requires its corporation and account, not an application agent ID")
        if self.connection_mode != "customer_service" and self.open_kfid is not None:
            raise ValueError("Customer-service account belongs only to its own mode")
        return self


class DiscordSettings(EmptySettings):
    connection_mode: Literal["webhook", "gateway"]
    public_key: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def require_webhook_key(self) -> "DiscordSettings":
        if self.connection_mode == "webhook" and self.public_key is None:
            raise ValueError("Discord webhook requires a verification key")
        return self


class TeamsSettings(EmptySettings):
    tenant_id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9.-]+$")


class DingTalkSettings(EmptySettings):
    connection_mode: Literal["stream"]
    robot_code: str = Field(min_length=1, max_length=512)


class WeChatSettings(EmptySettings):
    connection_mode: Literal["long_poll"]
    base_url: str = Field(min_length=1, max_length=2048)
    channel_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "WeChatSettings":
        from urllib.parse import urlsplit
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("WeChat endpoint is invalid")
        return self


def validate_settings(provider: Provider, encoded: str) -> str:
    try:
        if len(encoded) > 16384 or len(encoded.encode()) > 16384:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise InvalidInput("Channel settings exceed their byte bound") from None
    model = {"feishu": FeishuSettings, "wecom": WeComSettings,
        "discord": DiscordSettings, "teams": TeamsSettings, "dingtalk": DingTalkSettings,
        "wechat": WeChatSettings}.get(provider, EmptySettings)
    try:
        return model.model_validate_json(encoded).model_dump_json()
    except (ValidationError, ValueError):
        raise InvalidInput("Channel provider settings are invalid") from None
