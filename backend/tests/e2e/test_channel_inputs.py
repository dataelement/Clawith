"""Signed Slack ingress, stable Session routing and actual adapter delivery."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.channel_inputs import ChannelInputs
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.channel.contracts import AttachmentReference, InboundResult, IncomingMessage
from app.modules.channel.public import ChannelContextCodec, ChannelService, ChannelSyncCursors
from app.modules.credential.public import Secret
from app.modules.session.public import SessionService


def slack_event(identity, text, *, actor="U1", reply_to=None, files=None, conversation="D1"):
    event = {"type": "message" if conversation.startswith("D") else "app_mention", "user": actor, "channel": conversation, "text": text}
    if reply_to is not None:
        event["thread_ts"] = reply_to
    if files is not None:
        event["files"] = files
    payload = json.dumps(
        {"type": "event_callback", "team_id": "T1", "api_app_id": "A1", "event_id": identity, "event": event}
    ).encode()
    stamp = str(int(datetime.now(UTC).timestamp()))
    signature = "v0=" + hmac.new(b"signing-test", b"v0:" + stamp.encode() + b":" + payload, hashlib.sha256).hexdigest()
    return payload, {
        "content-type": "application/json",
        "x-slack-request-timestamp": stamp,
        "x-slack-signature": signature,
    }


async def slack_configuration(app, sessions, client):
    principal, agent, _ = await configure_agent(app.state.execution, sessions)
    async with transaction(sessions) as tx:
        credential = await app.state.execution.credentials(tx).create(
            principal,
            kind="channel",
            provider="slack",
            label="Slack bot",
            secret=Secret(json.dumps({"version": 1, "token": "xoxb-test", "signing_secret": "signing-test"})),
            owner_kind="agent",
            owner_id=agent.id,
        )
    await app.state.auth.provision_trusted_verifier(
        account_id=principal.account_id, login_name="channel-admin", password="password"
    )
    login = await client.post(
        "/api/auth/login",
        json={"login_name": "channel-admin", "password": "password", "tenant_id": str(principal.tenant_id)},
    )
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    configured = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "agent_id": str(agent.id),
            "provider": "slack",
            "external_identity": "T1:A1",
            "credential_id": str(credential.id),
        },
    )
    assert configured.status_code == 201, configured.text
    channel_id = UUID(configured.json()["id"])
    bound = await client.post(
        f"/api/channels/{channel_id}/actors",
        headers=headers,
        json={"external_actor_id": "U1", "membership_id": str(principal.membership_id)},
    )
    assert bound.status_code == 204, bound.text
    return principal, agent, channel_id


async def wait_messages(outgoing, count):
    async with asyncio.timeout(10):
        while len(outgoing) < count:
            await asyncio.sleep(0.02)


async def test_slack_signed_messages_reuse_session_resume_explicit_wait_and_deliver_once(
    test_database, composed_database, tmp_path, monkeypatch  # noqa: F811
):
    outgoing, models = [], []

    def peer(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "slack.com":
            assert request.url.path == "/api/chat.postMessage"
            assert request.headers["authorization"] == "Bearer xoxb-test"
            outgoing.append(body)
            return httpx.Response(200, json={"ok": True, "channel": "D1", "ts": f"1000.{len(outgoing)}"})
        if request.method == "GET":
            return httpx.Response(404)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        models.append(body)
        calls = [item for message in body["messages"] for item in message.get("tool_calls", [])]
        ids = {item["id"] for item in calls}
        current = next(
            message
            for message in body["messages"]
            if message["role"] == "user" and "initial_input:" in json.dumps(message["content"])
        )
        if "New work" in json.dumps(current):
            return (
                call("send_message", "new-answer", {"text": "New answer"})
                if not ids
                else response({"content": "new final"})
            )
        if "ack" not in ids:
            return call("send_message", "ack", {"text": "Started"})
        if "question" not in ids:
            return call("need_input", "question", {"question": "Which format?"})
        if "answer" not in ids:
            return call("send_message", "answer", {"text": "Markdown answer"})
        return response({"content": "Execution finished; not another Slack message"})

    monkeypatch.setattr(
        composition,
        "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs),
    )
    app = create_app(configured(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        principal, _, channel_id = await slack_configuration(app, test_database.sessions, client)
        endpoint = f"/api/channels/{principal.tenant_id}/{channel_id}/events"
        body, headers = slack_event("E1", "Prepare report")
        assert (
            await client.post(endpoint, content=body, headers={**headers, "x-slack-signature": "wrong"})
        ).status_code == 403
        first = await client.post(endpoint, content=body, headers=headers)
        assert first.status_code == 200, first.text
        await wait_messages(outgoing, 2)
        assert [message["text"] for message in outgoing] == ["Started", "Which format?"]
        async with transaction(test_database.sessions) as tx:
            page = await SessionService(tx).list(principal)
            assert len(page.sessions) == 1
            session = page.sessions[0]
            work = await SessionService(tx).list_work(principal, session_id=session.id)
            original_run = work.work[0].run_id
        reply_body, reply_headers = slack_event("E2", "Markdown", reply_to="1000.2")
        assert (await client.post(endpoint, content=reply_body, headers=reply_headers)).status_code == 200
        await eventually(test_database.sessions, principal.tenant_id, original_run, "Completed")
        await wait_messages(outgoing, 3)
        new_body, new_headers = slack_event("E3", "New work")
        assert (await client.post(endpoint, content=new_body, headers=new_headers)).status_code == 200
        await wait_messages(outgoing, 4)
        assert (await client.post(endpoint, content=new_body, headers=new_headers)).status_code == 200
        unknown_body, unknown_headers = slack_event("unknown", "Not mapped", actor="U2")
        assert (await client.post(endpoint, content=unknown_body, headers=unknown_headers)).status_code == 404
        async with transaction(test_database.sessions) as tx:
            assert len((await SessionService(tx).list(principal)).sessions) == 1
            work = await SessionService(tx).list_work(principal, session_id=session.id)
            assert len(work.work) == 2
            history = await SessionService(tx).read_history(principal, session_id=session.id)
            assert [item.content.text for item in history.entries] == [
                "Prepare report",
                "Started",
                "Which format?",
                "Markdown",
                "Markdown answer",
                "New work",
                "New answer",
            ]
        assert [message["text"] for message in outgoing] == [
            "Started",
            "Which format?",
            "Markdown answer",
            "New answer",
        ]
        channels = app.state.channel_inputs
    assert channels._task.done()


async def test_durable_channel_cursor_recovers_message_commit_before_enqueue_without_run_replay(
    test_database, composed_database, tmp_path, monkeypatch  # noqa: F811
):
    entered, release = asyncio.Event(), asyncio.Event()
    outgoing = []

    async def peer(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "slack.com":
            outgoing.append(body)
            return httpx.Response(200, json={"ok": True, "channel": "D1", "ts": "2000.1"})
        if request.method == "GET":
            return httpx.Response(404)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(message["role"] == "tool" for message in body["messages"]):
            entered.set()
            await release.wait()
            return call("send_message", "message", {"text": "Recovered message"})
        return response({"content": "done"})

    monkeypatch.setattr(
        composition,
        "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs),
    )
    settings = configured(tmp_path)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        principal, _, channel_id = await slack_configuration(app, test_database.sessions, client)
        body, headers = slack_event("crash-window", "Work")
        accepted = await client.post(
            f"/api/channels/{principal.tenant_id}/{channel_id}/events", content=body, headers=headers
        )
        assert accepted.status_code == 200, accepted.text
        await asyncio.wait_for(entered.wait(), 10)
        await app.state.channel_inputs.close()
        release.set()
        async with transaction(test_database.sessions) as tx:
            session = (await SessionService(tx).list(principal)).sessions[0]
            run_id = (await SessionService(tx).list_work(principal, session_id=session.id)).work[0].run_id
        await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
        assert not outgoing
        codec = ChannelContextCodec(
            active_key_version=settings.EXECUTION.continuation_keys.active_version,
            keys=settings.EXECUTION.continuation_keys.decoded_keys(),
        )
        recovered = ChannelInputs(app.state.database, app.state.execution, app.state.products, context_codec=codec)
        await recovered.startup()
        try:
            await wait_messages(outgoing, 1)
            async with transaction(test_database.sessions) as tx:
                assert len((await SessionService(tx).list_work(principal, session_id=session.id)).work) == 1
                sources = await ChannelService(tx).delivery_sources(kind="session")
                assert sources[0].cursor == 2
            await recovered._scan_messages()
            assert len(outgoing) == 1
        finally:
                await recovered.close()


async def test_wechat_qr_confirmation_publishes_credential_and_closes_owned_poll(
    test_database, composed_database, tmp_path, monkeypatch  # noqa: F811
):
    poll_started, poll_closed = asyncio.Event(), asyncio.Event()
    confirmations = 0
    async def peer(request):
        nonlocal confirmations
        if request.url.host.endswith("weixin.qq.com"):
            if "get_bot_qrcode" in request.url.path:
                return httpx.Response(200, json={"ret": 0, "qrcode": "private-qr", "qrcode_img_content": "https://ilinkai.weixin.qq.com/qr.png"})
            if "get_qrcode_status" in request.url.path:
                confirmations += 1
                return httpx.Response(200, json={"ret": 0, "status": "confirmed", "bot_token": "private-wechat-token",
                    "ilink_bot_id": "wechat-bot", "baseurl": "https://ilinkai.weixin.qq.com"})
            if "getupdates" in request.url.path:
                assert request.headers["authorization"] == "Bearer private-wechat-token"
                poll_started.set()
                try:
                    await asyncio.Future()
                finally:
                    poll_closed.set()
        if request.method == "GET":
            return httpx.Response(404)
        return call("capability_probe", "probe", {"value": "ok"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name="qr-admin", password="password")
        login = await client.post("/api/auth/login", json={"login_name": "qr-admin", "password": "password", "tenant_id": str(principal.tenant_id)})
        headers = {"Authorization": "Bearer " + login.json()["token"]}
        qr = await client.post("/api/channels/wechat/qr", headers=headers, json={"agent_id": str(agent.id)})
        assert qr.status_code == 201, qr.text
        assert "private-qr" not in qr.text
        path = f"/api/channels/wechat/qr/{qr.json()['request_id']}/status"
        result, duplicate = await asyncio.gather(*(client.post(path, headers=headers, json={}) for _ in range(2)))
        assert result.status_code == duplicate.status_code == 200
        assert result.json()["channel_id"] == duplicate.json()["channel_id"] and confirmations == 1
        assert "private-wechat-token" not in result.text
        async with transaction(test_database.sessions) as tx:
            channel = await ChannelService(tx).get(principal, channel_id=UUID(result.json()["channel_id"]))
            secret = await app.state.execution.credentials(tx).reveal_secret_for_owner(tenant_id=principal.tenant_id,
                credential_id=channel.credential_id, owner_kind="agent", owner_id=agent.id)
            assert json.loads(secret.value)["bot_token"] == "private-wechat-token"
        await asyncio.wait_for(poll_started.wait(), 5)
        channels = app.state.channel_inputs
    assert poll_closed.is_set() and not channels._listeners


async def test_customer_service_cursor_resumes_transport_without_repeating_accepted_message(
    test_database, composed_database, tmp_path, monkeypatch  # noqa: F811
):
    from modules.channel import test_wecom_provider as wire
    second_page = asyncio.Event()
    allow_second = False
    calls, delivered = [], []
    async def peer(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "qyapi.weixin.qq.com":
            if request.url.path.endswith("/gettoken"):
                return httpx.Response(200, json={"errcode": 0, "access_token": "application-token"})
            if request.url.path.endswith("/kf/sync_msg"):
                coordinate = body.get("token") or body.get("cursor")
                calls.append(coordinate)
                if coordinate == "next-private-cursor" and not allow_second:
                    second_page.set()
                    await asyncio.Future()
                ids = ["message-A"] if coordinate == "notice-private-token" else ["message-A", "message-B"]
                return httpx.Response(200, json={"errcode": 0, "has_more": int(coordinate == "notice-private-token"),
                    "next_cursor": "next-private-cursor", "msg_list": [{"origin": 3, "msgtype": "text", "msgid": id,
                        "open_kfid": "K1", "external_userid": "customer", "text": {"content": id}} for id in ids]})
            assert request.url.path.endswith("/kf/send_msg"), request.url.path
            delivered.append(body)
            return httpx.Response(200, json={"errcode": 0, "msgid": f"reply-{len(delivered)}"})
        if request.method == "GET":
            return httpx.Response(404)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(message["role"] == "tool" for message in body["messages"]):
            return call("send_message", "response", {"text": "Customer-service response"})
        return response({"content": "done"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    monkeypatch.setattr(wire, "NOW", datetime.now(UTC))
    settings = configured(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            credential = await app.state.execution.credentials(tx).create(principal, kind="channel", provider="wecom", label="Customer service",
                secret=wire.credential(), owner_kind="agent", owner_id=agent.id)
            channel = await ChannelService(tx).configure(principal, agent_id=agent.id, provider="wecom", external_identity="corp:kf:K1",
                credential_id=credential.id, settings_json=json.dumps({"connection_mode": "customer_service", "corp_id": "corp", "open_kfid": "K1"}))
            await ChannelService(tx).bind_actor(principal, channel_id=channel.id, external_actor_id="customer", membership_id=principal.membership_id)
        raw, headers = wire.callback(b"<xml><ToUserName>corp</ToUserName><Event>kf_msg_or_event</Event><OpenKfId>K1</OpenKfId><Token>notice-private-token</Token></xml>")
        endpoint = f"/api/channels/{principal.tenant_id}/{channel.id}/kf/events"
        accepted = await client.post(endpoint, content=raw, headers=headers)
        assert accepted.status_code == 200, accepted.text
        await asyncio.wait_for(second_page.wait(), 8)
        await app.state.channel_inputs.close()
        allow_second = True
        codec = ChannelContextCodec(active_key_version=settings.EXECUTION.continuation_keys.active_version,
            keys=settings.EXECUTION.continuation_keys.decoded_keys())
        recovered = ChannelInputs(app.state.database, app.state.execution, app.state.products, context_codec=codec)
        await recovered.startup()
        try:
            async with asyncio.timeout(10):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        pending = await ChannelSyncCursors(tx, codec).pending()
                    if not pending and len(delivered) == 2:
                        break
                    await asyncio.sleep(.02)
            async with transaction(test_database.sessions) as tx:
                sessions = await SessionService(tx).list(principal)
                assert len(sessions.sessions) == 1
                work = await SessionService(tx).list_work(principal, session_id=sessions.sessions[0].id)
                assert len(work.work) == 2
            assert calls == ["notice-private-token", "next-private-cursor", "next-private-cursor"]
            assert all(item["open_kfid"] == "K1" and item["touser"] == "customer" for item in delivered)
        finally:
            await recovered.close()


@pytest.mark.parametrize("reject_second", [False, True])
async def test_slack_two_files_are_materialized_and_read_through_actual_attachment_tool(
        test_database, composed_database, tmp_path, monkeypatch, reject_second):  # noqa: F811
    import re

    outgoing, observed, downloaded, published = [], [], [], []

    def peer(request):
        if request.url.host == "files.slack.com":
            if request.method == "POST":
                assert request.content in (b"contents-F1", b"contents-F2")
                return httpx.Response(200)
            assert request.headers["authorization"] == "Bearer xoxb-test"
            identity = request.url.path.rsplit("/", 1)[-1]
            downloaded.append(identity)
            return httpx.Response(200, content=("contents-" + identity).encode())
        if request.url.host == "slack.com":
            if request.url.path == "/api/files.getUploadURLExternal":
                identity = request.url.params["filename"]
                return httpx.Response(200, json={"ok": True, "file_id": identity,
                    "upload_url": "https://files.slack.com/upload/" + identity})
            if request.url.path == "/api/files.completeUploadExternal":
                body = json.loads(request.content)
                assert body["channel_id"] == "D1"
                published.append(body["files"][0]["id"])
                if reject_second and len(published) == 2:
                    return httpx.Response(200, json={"ok": False, "error": "publication_rejected"})
                return httpx.Response(200, json={"ok": True, "files": [{"id": published[-1]}]})
            if request.url.path == "/api/files.info":
                identity = request.url.params["file"]
                return httpx.Response(200, json={"ok": True, "file": {"id": identity,
                    "url_private_download": "https://files.slack.com/private/" + identity}})
            outgoing.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "channel": "D1", "ts": "1001.1"})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        results = {message.get("tool_call_id") for message in body["messages"] if message["role"] == "tool"}
        if "read_attachment" not in names:
            return call("search_tools", "find-reader", {"query": "read_attachment"})
        if "answer" in results:
            return response({"content": "Done"})
        refs = sorted(set(re.findall(r"attachment:session:[0-9a-f-]{36}", json.dumps(body))))
        assert len(refs) == 2
        for index, ref in enumerate(refs):
            if f"read-{index}" not in results:
                return call("read_attachment", f"read-{index}", {"reference": ref})
        assert "contents-F1" in json.dumps(body) and "contents-F2" in json.dumps(body)
        if "answer" not in results:
            return call("send_message", "answer", {"text": "Both files inspected",
                "references": [{"reference": ref} for ref in refs]})
        return response({"content": "Done"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, _, channel_id = await slack_configuration(app, test_database.sessions, client)
        endpoint = f"/api/channels/{principal.tenant_id}/{channel_id}/events"
        files = [{"id": identity, "name": identity + ".txt", "mimetype": "text/plain"} for identity in ("F1", "F2")]
        body, headers = slack_event("media", "Read both files", files=files)
        accepted = await client.post(endpoint, content=body, headers=headers)
        assert accepted.status_code == 200, accepted.text
        await wait_messages(outgoing, 1)
        await wait_messages(published, 2)
        assert (await client.post(endpoint, content=body, headers=headers)).status_code == 200
        assert downloaded == ["F1", "F2"]
        assert sorted(published) == ["F1.txt", "F2.txt"]
        assert "contents-F1" not in json.dumps(observed[0])
        assert "files.slack.com" not in json.dumps(observed)
        async with transaction(test_database.sessions) as tx:
            session = (await SessionService(tx).list(principal)).sessions[0]
            work = await SessionService(tx).list_work(principal, session_id=session.id)
            assert len(work.work) == 1
        await eventually(test_database.sessions, principal.tenant_id, work.work[0].run_id, "Completed")
        from sqlalchemy import select

        from app.modules.channel.models import ChannelDeliveryRecord
        async with asyncio.timeout(5):
            while True:
                async with transaction(test_database.sessions) as tx:
                    rows = (await tx.session.scalars(select(ChannelDeliveryRecord).where(
                        ChannelDeliveryRecord.channel_configuration_id == channel_id))).all()
                    if len(rows) == 1 and rows[0].delivery_status == ("uncertain" if reject_second else "delivered"):
                        delivery_id = rows[0].id
                        break
                await asyncio.sleep(.02)
        repeated = await app.state.channel_inputs.delivery.send(tenant_id=principal.tenant_id, delivery_id=delivery_id)
        assert repeated.status == ("uncertain" if reject_second else "delivered")
        assert len(published) == 2


@pytest.mark.parametrize("provider", ["feishu", "dingtalk"])
async def test_authenticated_native_media_reaches_product_attachment_owner(
        test_database, composed_database, tmp_path, monkeypatch, provider):  # noqa: F811
    fetched, seen, published = [], [], []
    def peer(request):
        if request.url.host == "open.feishu.cn":
            if "tenant_access_token" in request.url.path:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
            if request.url.path.endswith("/files"):
                assert b"native-media-content" in request.content
                return httpx.Response(200, json={"code": 0, "data": {"file_key": "uploaded"}})
            if request.url.path.endswith("/messages"):
                payload = json.loads(request.content)
                assert payload["msg_type"] == "file" and json.loads(payload["content"]) == {"file_key": "uploaded"}
                published.append(payload)
                return httpx.Response(200, json={"code": 0, "data": {"message_id": "published"}})
            assert "/messages/msg/resources/key" in request.url.path
            fetched.append(provider)
            return httpx.Response(200, content=b"native-media-content", headers={"content-type": "text/plain"})
        if request.url.host == "api.dingtalk.com":
            if request.url.path.endswith("accessToken"):
                return httpx.Response(200, json={"accessToken": "private-token"})
            assert request.url.path.endswith("download")
            return httpx.Response(200, json={"downloadUrl": "https://cdn.dingtalk.com/private"})
        if request.url.host == "cdn.dingtalk.com":
            fetched.append(provider)
            return httpx.Response(200, content=b"native-media-content", headers={"content-type": "text/plain"})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        seen.append(body)
        if provider == "feishu" and not any(message.get("tool_call_id") == "send-file" for message in body["messages"]):
            import re
            reference = re.search(r"attachment:session:[0-9a-f-]{36}", json.dumps(body))[0]
            return call("send_message", "send-file", {"text": "", "references": [{"reference": reference}]})
        return response({"content": "Done"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.channel_inputs.close()
        async with transaction(test_database.sessions) as tx:
            secret = {"version": 1, "app_secret": "private"}
            if provider == "feishu":
                secret.update(app_id="cli-app", verification_token="verify", encrypt_key="")
            credential = await app.state.execution.credentials(tx).create(principal, kind="channel", provider=provider,
                label="Native media", secret=Secret(json.dumps(secret)), owner_kind="agent", owner_id=agent.id)
            settings = {"connection_mode": "webhook", "bot_open_id": "bot", "tenant_key": "tenant"} if provider == "feishu" else {"connection_mode": "stream", "robot_code": "robot"}
            channel = await ChannelService(tx).configure(principal, agent_id=agent.id, provider=provider,
                external_identity="cli-app" if provider == "feishu" else "app-key", credential_id=credential.id,
                settings_json=json.dumps(settings))
            await ChannelService(tx).bind_actor(principal, channel_id=channel.id, external_actor_id="human",
                membership_id=principal.membership_id)
        # The native listener owns authentication; this is its typed application boundary.
        message = IncomingMessage("media", "human", "conversation", None, "Inspect file", None,
            (AttachmentReference("msg/key" if provider == "feishu" else "handle", "report.txt", "text/plain"),))
        channels = ChannelInputs(app.state.database, app.state.execution, app.state.products,
            context_codec=ChannelContextCodec(keys={"test": b"c" * 32}, active_key_version="test"))
        if provider == "feishu":
            await channels.startup()
        await channels.accept_authenticated(channel, InboundResult(message=message))
        async with transaction(test_database.sessions) as tx:
            session = (await SessionService(tx).list(principal)).sessions[0]
            history = await SessionService(tx).read_history(principal, session_id=session.id)
            assert history.entries[0].content.references[0].reference.startswith("attachment:session:")
            work = await SessionService(tx).list_work(principal, session_id=session.id)
        await eventually(test_database.sessions, principal.tenant_id, work.work[0].run_id, "Completed")
        assert fetched == [provider]
        assert "native-media-content" not in json.dumps(seen)
        if provider == "feishu":
            await wait_messages(published, 1)
        await channels.close()


async def test_discord_gateway_quoted_middle_waiting_fragment_resumes_same_run(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    from app.modules.channel.public import DiscordAdapter

    published, sockets_closed = [], []
    app = None
    channel = None
    def peer(request):
        if request.url.host == "discord.com":
            published.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(1000 + len(published)), "channel_id": "789"})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(item.get("tool_call_id") == "question" for item in body["messages"]):
            return call("need_input", "question", {"question": "Q" * 4500})
        return response({"content": "Answered"})
    async def gateway(socket):
        try:
            await socket.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 10000}}))
            assert json.loads(await socket.recv())["op"] == 2
            await socket.send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {
                "application": {"id": "123"}, "user": {"id": "456"}, "session_id": "gateway",
                "resume_gateway_url": "wss://gateway.discord.gg"}}))
            await socket.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": {
                "id": "800", "channel_id": "789", "author": {"id": "567"}, "content": "Work"}}))
            async with asyncio.timeout(10):
                while True:
                    if channel is not None:
                        async with transaction(test_database.sessions) as tx:
                            found = await ChannelService(tx).delivered_reply(tenant_id=channel.tenant_id,
                                channel_id=channel.id, destination="789", acknowledgement="1002")
                        if found is not None:
                            break
                    await asyncio.sleep(.02)
            await socket.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 3, "d": {
                "id": "801", "channel_id": "789", "author": {"id": "567"}, "content": "Answer",
                "message_reference": {"message_id": "1002", "channel_id": "789"}}}))
            async for raw in socket:
                if json.loads(raw)["op"] == 1:
                    await socket.send('{"op":11,"d":null}')
        finally:
            sockets_closed.append(True)
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    async with serve(gateway, "127.0.0.1", 0) as server:
        app = create_app(configured(tmp_path))
        async with app.router.lifespan_context(app):
            adapter = next(item for item in app.state.channel_inputs.adapters.adapters if isinstance(item, DiscordAdapter))
            adapter._connector = lambda _: connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", proxy=None)
            principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
            async with transaction(test_database.sessions) as tx:
                credential = await app.state.execution.credentials(tx).create(principal, kind="channel", provider="discord",
                    label="Gateway", secret=Secret('{"version":1,"bot_token":"test-token"}'), owner_kind="agent", owner_id=agent.id)
                channel = await ChannelService(tx).configure(principal, agent_id=agent.id, provider="discord", external_identity="123",
                    credential_id=credential.id, settings_json='{"connection_mode":"gateway"}')
                await ChannelService(tx).bind_actor(principal, channel_id=channel.id, external_actor_id="567", membership_id=principal.membership_id)
            async with asyncio.timeout(15):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        sessions = (await SessionService(tx).list(principal)).sessions
                        work = await SessionService(tx).list_work(principal, session_id=sessions[0].id) if sessions else None
                        history = await SessionService(tx).read_history(principal, session_id=sessions[0].id) if sessions else None
                    if history is not None and len(history.entries) == 3:
                        break
                    await asyncio.sleep(.02)
            assert len(work.work) == 1
            await eventually(test_database.sessions, principal.tenant_id, work.work[0].run_id, "Completed")
            assert [len(item["content"]) for item in published] == [2000, 2000, 500]
        assert sockets_closed


async def test_dingtalk_transport_disconnect_reconnects_without_restarting_accepted_input(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    from app.modules.channel.public import DingTalkAdapter

    connections, closed = [], []
    second = asyncio.Event()
    def peer(request):
        if request.url.host == "api.dingtalk.com":
            assert request.url.path.endswith("/gateway/connections/open")
            return httpx.Response(200, json={"endpoint": "wss://stream.dingtalk.com/path", "ticket": "private"})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        return response({"content": "Done"})
    async def stream(socket):
        identity = len(connections)
        connections.append(identity)
        data = {"msgId": "stable-event", "senderStaffId": "staff", "robotCode": "robot", "conversationId": "cid",
            "conversationType": "1", "msgtype": "text", "text": {"content": "Work"}}
        try:
            await socket.send(json.dumps({"type": "CALLBACK", "headers": {"messageId": "frame",
                "topic": "/v1.0/im/bot/messages/get"}, "data": json.dumps(data)}))
            assert json.loads(await socket.recv())["code"] == 200
            if identity == 0:
                await socket.close(code=1001)
            else:
                second.set()
                await socket.wait_closed()
        finally:
            closed.append(identity)
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    async with serve(stream, "127.0.0.1", 0) as server:
        app = create_app(configured(tmp_path))
        async with app.router.lifespan_context(app):
            adapter = next(item for item in app.state.channel_inputs.adapters.adapters if isinstance(item, DingTalkAdapter))
            adapter.connector = lambda _: connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", proxy=None)
            principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
            async with transaction(test_database.sessions) as tx:
                credential = await app.state.execution.credentials(tx).create(principal, kind="channel", provider="dingtalk",
                    label="Stream", secret=Secret('{"version":1,"app_secret":"test-token"}'), owner_kind="agent", owner_id=agent.id)
                channel = await ChannelService(tx).configure(principal, agent_id=agent.id, provider="dingtalk", external_identity="app-key",
                    credential_id=credential.id, settings_json='{"connection_mode":"stream","robot_code":"robot"}')
                await ChannelService(tx).bind_actor(principal, channel_id=channel.id, external_actor_id="staff", membership_id=principal.membership_id)
            await asyncio.wait_for(second.wait(), timeout=12)
            assert channel.id not in app.state.channel_inputs.listener_failures
            async with transaction(test_database.sessions) as tx:
                sessions = (await SessionService(tx).list(principal)).sessions
                assert len(sessions) == 1
                work = await SessionService(tx).list_work(principal, session_id=sessions[0].id)
                assert len(work.work) == 1
            await eventually(test_database.sessions, principal.tenant_id, work.work[0].run_id, "Completed")
        assert sorted(closed) == [0, 1]
