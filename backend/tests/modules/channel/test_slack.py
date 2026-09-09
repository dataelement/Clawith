import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.adapters import SlackAdapter
from app.modules.channel.public import ChannelView, DeliveryContent
from app.modules.credential.public import Secret

NOW = datetime(2026, 9, 9, tzinfo=UTC)
SECRET = Secret('{"version":1,"token":"bot-secret","signing_secret":"sign-secret"}')


def channel():
    agent = uuid4()
    return ChannelView(uuid4(), uuid4(), agent, "slack", "T1:A1", uuid4(), True, "agent", agent)


def signed(payload, stamp=None):
    body = json.dumps(payload).encode()
    stamp = stamp or str(int(NOW.timestamp()))
    signature = "v0=" + hmac.new(b"sign-secret", b"v0:" + stamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return body, {"X-Slack-Request-Timestamp":stamp, "X-Slack-Signature":signature}


async def test_signed_human_event_and_workspace_identity_and_bot_filter():
    payload = {"type":"event_callback", "team_id":"T1","api_app_id":"A1", "event_id":"event",
        "event":{"type":"message", "channel":"D1", "user":"U1", "text":"hello"}}
    async with create_stateless_http_client() as http:
        adapter = SlackAdapter(http)
        raw, headers = signed(payload)
        incoming = (await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)).message
        assert (incoming.actor_id, incoming.text, incoming.group_id) == ("U1", "hello", None)
        payload["team_id"] = "T2"
        raw, headers = signed(payload)
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)
        payload["team_id"] = "T1"
        payload["event"]["bot_id"] = "B1"
        raw, headers = signed(payload)
        assert (await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)).message is None


async def test_challenge_replay_bad_signature_and_attachment_references():
    async with create_stateless_http_client() as http:
        adapter = SlackAdapter(http)
        raw, headers = signed({"type":"url_verification", "challenge":"value"})
        assert (await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)).challenge == "value"
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), SECRET, body=raw+b" ", headers=headers, now=NOW)
        raw, headers = signed({"type":"url_verification", "challenge":"value"}, str(int(NOW.timestamp())-301))
        with pytest.raises(AccessDenied):
            await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)
        raw, headers = signed({"type":"event_callback", "team_id":"T1","api_app_id":"A1", "event_id":"file-event", "event":{
            "type":"message", "subtype":"file_share", "channel":"D1", "user":"U1", "text":"",
            "files":[{"id":"F1", "name":"image.png", "mimetype":"image/png"}]}})
        message = (await adapter.receive(channel(), SECRET, body=raw, headers=headers, now=NOW)).message
        assert message.attachments[0].external_id == "F1"
        with pytest.raises(InvalidInput):
            await adapter.receive(channel(), SECRET, body=b"x"*262145, headers=headers, now=NOW)


@pytest.mark.parametrize("mode,expected", [("success","delivered"),("rejected","failed"),("timeout","uncertain"),("server","uncertain")])
async def test_real_http_send_adapter_normalizes_outcomes_without_replay(mode, expected):
    requests = []
    def peer(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer bot-secret"
        assert "cookie" not in request.headers
        assert json.loads(request.content) == {"channel":"D1", "text":"reply"}
        if mode == "timeout":
            raise httpx.ReadTimeout("secret provider response", request=request)
        if mode == "server":
            return httpx.Response(500)
        if mode == "rejected":
            return httpx.Response(200, json={"ok":False, "error":"invalid_auth"})
        return httpx.Response(200, json={"ok":True, "channel":"D1", "ts":"123.4"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        outcome = await SlackAdapter(http).send(channel(), SECRET, destination="D1", content=DeliveryContent("reply"), delivery_key="key")
    assert outcome.status == expected and len(requests) == 1
    assert "secret" not in repr(outcome)
