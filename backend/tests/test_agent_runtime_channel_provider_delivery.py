"""Provider routing tests for durable Runtime channel deliveries."""

from collections import deque
from types import SimpleNamespace
import uuid

import pytest

from app.api import teams
from app.services import feishu_service, wechat_channel, wecom_stream
from app.services.agent_runtime import channel_provider_delivery
from app.services.agent_runtime.channel_delivery import ChannelDeliveryEnvelope
from app.services.agent_runtime.channel_provider_delivery import (
    DatabaseChannelDeliverySender,
)


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, config) -> None:
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        return _Result(self.config)


class _Factory:
    def __init__(self, config) -> None:
        self.config = config

    def __call__(self):
        return _Session(self.config)


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = str(payload)

    def json(self):
        return self.payload


class _HTTPClient:
    def __init__(self, *responses: _Response) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def _request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.popleft()

    async def get(self, url: str, **kwargs):
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs):
        return await self._request("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs):
        return await self._request("PATCH", url, **kwargs)


def _config(
    *,
    app_id: str = "app-1",
    app_secret: str = "secret-1",
    extra_config: dict | None = None,
):
    return SimpleNamespace(
        app_id=app_id,
        app_secret=app_secret,
        extra_config=extra_config or {},
        is_configured=True,
    )


def _envelope(channel: str, target: dict) -> ChannelDeliveryEnvelope:
    return ChannelDeliveryEnvelope(
        delivery_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        channel=channel,
        target=target,
        content="Durable provider reply",
        idempotency_key="run:1:terminal:completed",
        attempt_count=1,
    )


def _sender(config) -> DatabaseChannelDeliverySender:
    return DatabaseChannelDeliverySender(
        session_factory=_Factory(config),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_feishu_delivery_loads_credentials_but_persists_only_destination(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def send_message(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {"code": 0, "data": {"message_id": "om-1"}}

    monkeypatch.setattr(feishu_service.feishu_service, "send_message", send_message)
    result = await _sender(_config()).send(
        _envelope(
            "feishu",
            {"receive_id": "oc-1", "receive_id_type": "chat_id"},
        )
    )

    assert result.provider_message_id == "om-1"
    assert calls["args"][:3] == ("app-1", "secret-1", "oc-1")  # type: ignore[index]
    assert calls["kwargs"]["stage"] == "runtime_channel_delivery"  # type: ignore[index]


@pytest.mark.asyncio
async def test_dingtalk_delivery_uses_the_persisted_session_webhook(monkeypatch) -> None:
    client = _HTTPClient(_Response({"errcode": 0}))
    monkeypatch.setattr(
        channel_provider_delivery.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )

    await _sender(_config()).send(
        _envelope(
            "dingtalk",
            {
                "session_webhook": "https://dingtalk.example/session",
                "user_id": "staff-1",
                "title": "Runtime Agent",
            },
        )
    )

    assert client.calls[0][0:2] == (
        "POST",
        "https://dingtalk.example/session",
    )
    assert client.calls[0][2]["json"]["markdown"]["text"] == "Durable provider reply"


@pytest.mark.asyncio
async def test_wecom_websocket_delivery_survives_the_original_callback(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def send_message(agent_id, chat_id, content):
        calls["send"] = (agent_id, chat_id, content)

    monkeypatch.setattr(wecom_stream.wecom_stream_manager, "send_message", send_message)
    envelope = _envelope(
        "wecom",
        {
            "user_id": "staff-1",
            "chat_id": "group-1",
            "transport": "websocket",
        },
    )

    await _sender(
        _config(
            app_id="",
            app_secret="",
            extra_config={"connection_mode": "websocket"},
        )
    ).send(envelope)

    assert calls["send"] == (
        envelope.agent_id,
        "group-1",
        "Durable provider reply",
    )


@pytest.mark.asyncio
async def test_wecom_customer_service_claims_session_before_delivery(monkeypatch) -> None:
    client = _HTTPClient(
        _Response({"errcode": 0, "access_token": "access-1"}),
        _Response({"errcode": 0}),
        _Response({"errcode": 0, "msgid": "wecom-1"}),
    )
    monkeypatch.setattr(
        channel_provider_delivery.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )

    result = await _sender(_config()).send(
        _envelope(
            "wecom",
            {
                "user_id": "external-user-1",
                "is_kf": True,
                "open_kfid": "kf-1",
            },
        )
    )

    assert "/kf/service_state/trans" in client.calls[1][1]
    assert client.calls[1][2]["json"]["service_state"] == 1
    assert "/kf/send_msg" in client.calls[2][1]
    assert result.provider_message_id == "wecom-1"


@pytest.mark.asyncio
async def test_wechat_delivery_uses_the_latest_persisted_context(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def send_message(**kwargs):
        calls["send"] = kwargs

    monkeypatch.setattr(wechat_channel, "send_wechat_text_message", send_message)
    await _sender(
        _config(
            extra_config={
                "bot_token": "wechat-token",
                "baseurl": "https://wechat.example",
                "recent_context_tokens": {
                    "wechat-user-1": {
                        "context_token": "context-1",
                        "conv_id": "wechat-1",
                    }
                },
            }
        )
    ).send(_envelope("wechat", {"user_id": "wechat-user-1"}))

    assert calls["send"]["context_token"] == "context-1"  # type: ignore[index]
    assert calls["send"]["text"] == "Durable provider reply"  # type: ignore[index]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "target", "response", "expected_url_part", "expected_provider_id"),
    [
        (
            "slack",
            {"channel_id": "D123"},
            {"ok": True, "ts": "100.1"},
            "slack.com/api/chat.postMessage",
            "100.1",
        ),
        (
            "whatsapp",
            {"phone": "15551234567"},
            {"messages": [{"id": "wamid-1"}]},
            "graph.facebook.com",
            "wamid-1",
        ),
        (
            "discord",
            {
                "channel_id": "channel-1",
                "reply_to_message_id": "incoming-1",
            },
            {"id": "discord-message-1"},
            "/channels/channel-1/messages",
            "discord-message-1",
        ),
    ],
)
async def test_http_channel_provider_confirms_response_before_marking_delivered(
    monkeypatch,
    channel,
    target,
    response,
    expected_url_part,
    expected_provider_id,
) -> None:
    client = _HTTPClient(_Response(response))
    monkeypatch.setattr(
        channel_provider_delivery.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )

    result = await _sender(_config()).send(_envelope(channel, target))

    assert expected_url_part in client.calls[0][1]
    assert result.provider_message_id == expected_provider_id
    if channel == "discord":
        assert client.calls[0][2]["json"]["message_reference"] == {
            "message_id": "incoming-1",
            "fail_if_not_exists": False,
        }


@pytest.mark.asyncio
async def test_expired_discord_interaction_falls_back_to_channel(monkeypatch) -> None:
    client = _HTTPClient(
        _Response({"message": "Unknown Webhook"}, status_code=404),
        _Response({"id": "discord-fallback-1"}),
    )
    monkeypatch.setattr(
        channel_provider_delivery.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )

    result = await _sender(_config()).send(
        _envelope(
            "discord",
            {
                "channel_id": "channel-1",
                "interaction_token": "expired-token",
            },
        )
    )

    assert "/messages/@original" in client.calls[0][1]
    assert "/channels/channel-1/messages" in client.calls[1][1]
    assert result.provider_message_id == "discord-fallback-1"


@pytest.mark.asyncio
async def test_teams_delivery_reconstructs_the_activity_from_durable_target(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def send_message(config, conversation_id, activity):
        calls["config"] = config
        calls["conversation_id"] = conversation_id
        calls["activity"] = activity

    monkeypatch.setattr(teams, "_send_teams_message", send_message)
    envelope = _envelope(
        "microsoft_teams",
        {
            "conversation_id": "teams-conversation-1",
            "reply_to_id": "incoming-1",
            "bot_account": {"id": "bot-1"},
            "recipient": {"id": "user-1"},
        },
    )

    await _sender(
        _config(extra_config={"service_url": "https://teams.example"})
    ).send(envelope)

    assert calls["conversation_id"] == "teams-conversation-1"
    activity = calls["activity"]
    assert activity["id"] == str(envelope.delivery_id)  # type: ignore[index]
    assert activity["replyToId"] == "incoming-1"  # type: ignore[index]
    assert activity["text"] == "Durable provider reply"  # type: ignore[index]


@pytest.mark.asyncio
async def test_slack_business_error_is_retryable_worker_failure(monkeypatch) -> None:
    client = _HTTPClient(_Response({"ok": False, "error": "ratelimited"}))
    monkeypatch.setattr(
        channel_provider_delivery.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )

    with pytest.raises(RuntimeError, match="slack rejected delivery"):
        await _sender(_config()).send(
            _envelope("slack", {"channel_id": "D123"})
        )
