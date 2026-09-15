import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from e2e.test_direct_session import call
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.run.public import InputContent
from app.modules.session.public import SessionService


@asynccontextmanager
async def socket_connection(app, session_id, token, *, execution_gate=None):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})
    path = f"/api/sessions/{session_id}/events"
    scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "scheme": "ws",
        "path": path, "raw_path": path.encode(), "query_string": b"after_position=0", "root_path": "",
        "server": ("test", 80), "client": ("test", 1234),
        "headers": [(b"sec-websocket-protocol", f"clawith, auth.{token}".encode())], "subprotocols": ["clawith", f"auth.{token}"]}
    async def send(message):
        if (execution_gate is not None and message["type"] == "websocket.send"
                and json.loads(message["text"]).get("type") == "execution"):
            await execution_gate.wait()
        await outgoing.put(message)
    task = asyncio.create_task(app(scope, incoming.get, send))
    try:
        async with asyncio.timeout(5):
            assert (await outgoing.get())["type"] == "websocket.accept"
        yield outgoing, task
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        try:
            async with asyncio.timeout(5):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class Frames(httpx.AsyncByteStream):
    def __init__(self, frames, *, fail=False, barrier=None):
        self.frames, self.fail, self.barrier = frames, fail, barrier
        self.closed = False

    async def __aiter__(self):
        for frame in self.frames:
            yield ("data: " + json.dumps(frame) + "\n\n").encode()
            await asyncio.sleep(.01)
        if self.barrier is not None:
            await self.barrier.wait()
        if self.fail:
            raise httpx.ReadError("controlled mid-stream failure")
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


def delta(content, *, finish=None):
    return {"choices": [{"delta": {"content": content}, "finish_reason": finish}]}


async def test_real_sse_retry_discard_ws_and_reconnect_do_not_create_chat_replies(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    attempts, streams = [], []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        assert body["stream"] is True
        attempts.append(body)
        if len(attempts) == 1:
            frames = Frames([delta("Discard this partial attempt")], fail=True)
        elif len(attempts) == 2:
            frames = Frames([delta("Fresh attempt"), {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "reply",
                "function": {"name": "send_message", "arguments": '{"text":"Committed user reply"}'}}]}, "finish_reason": "tool_calls"}]}])
        else:
            frames = Frames([delta("Internal final result", finish="stop")])
        streams.append(frames)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=frames)
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions,
            capabilities={"supports_tool_calling": True, "supports_streaming": True})
        await app.state.auth.provision_trusted_verifier(account_id=p.account_id, login_name="stream", password="password")
        token, p = await app.state.auth.login("stream", "password", p.tenant_id)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
        received = []
        async with socket_connection(app, session.id, token) as (outgoing, _):
            intake = await app.state.products.submit_session(p, session_id=session.id, source_key="stream",
                input=InputContent("Please answer"))
            async with asyncio.timeout(10):
                while not any(item.get("event", {}).get("text") == "Internal final result" for item in received if item.get("event")):
                    message = await outgoing.get()
                    assert message["type"] == "websocket.send"
                    received.append(json.loads(message["text"]))
            await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Completed")
        assert app.state.products.streams.subscriptions == 0
        execution = [item for item in received if item["type"] == "execution"]
        assert [item["kind"] for item in execution].count("attempt_discarded") == 1
        first_step = execution[0]["step_id"]
        assert [item["attempt"] for item in execution if item["kind"] == "attempt_started" and item["step_id"] == first_step] == [1, 2]
        assert all(item["run_id"] == str(intake.run.id) for item in execution)
        async with socket_connection(app, session.id, token) as (outgoing, _):
            async with asyncio.timeout(5):
                history = json.loads((await outgoing.get())["text"])
            assert history["type"] == "history"
            assert [item["content"]["text"] for item in history["entries"]] == ["Please answer", "Committed user reply"]
            try:
                async with asyncio.timeout(.05):
                    unexpected = await outgoing.get()
                    raise AssertionError(f"Transient execution was replayed: {unexpected}")
            except TimeoutError:
                pass
        assert len(attempts) == 3 and all(stream.closed for stream in streams)


@pytest.mark.parametrize("reason", ["logout", "expiry"])
async def test_login_end_closes_stream_subscription_without_cancelling_active_run(test_database, composed_database, tmp_path, monkeypatch, reason):  # noqa: F811
    release = asyncio.Event()
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            stream=Frames([delta("Still working", finish="stop")], barrier=release))
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions,
            capabilities={"supports_tool_calling": True, "supports_streaming": True})
        await app.state.auth.provision_trusted_verifier(account_id=p.account_id, login_name="stream", password="password")
        token, p = await app.state.auth.login("stream", "password", p.tenant_id)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
        try:
            async with socket_connection(app, session.id, token) as (outgoing, connection):
                intake = await app.state.products.submit_session(p, session_id=session.id, source_key="running", input=InputContent("Long work"))
                if reason == "logout":
                    await app.state.auth.logout(token)
                else:
                    app.state.auth._clock = lambda: datetime.now(UTC) + timedelta(hours=25)
                async with asyncio.timeout(5):
                    while True:
                        message = await outgoing.get()
                        if message["type"] == "websocket.close":
                            assert message["code"] == 1008
                            break
                    await connection
                assert app.state.products.streams.subscriptions == 0
            release.set()
            await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Completed")
        finally:
            release.set()


async def test_slow_websocket_gets_explicit_overflow_resync_without_blocking_model(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    gate = asyncio.Event()
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            stream=Frames([delta(str(index)) for index in range(80)] + [delta("done", finish="stop")]))
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions,
            capabilities={"supports_tool_calling": True, "supports_streaming": True})
        await app.state.auth.provision_trusted_verifier(account_id=p.account_id, login_name="stream", password="password")
        token, p = await app.state.auth.login("stream", "password", p.tenant_id)
        async with transaction(test_database.sessions) as tx:
            session = await SessionService(tx).create(p, agent_id=agent.id)
        try:
            async with socket_connection(app, session.id, token, execution_gate=gate) as (outgoing, _):
                intake = await app.state.products.submit_session(p, session_id=session.id, source_key="slow", input=InputContent("Lots of stream events"))
                await eventually(test_database.sessions, p.tenant_id, intake.run.id, "Completed")
                assert not gate.is_set()
                gate.set()
                async with asyncio.timeout(5):
                    while True:
                        message = json.loads((await outgoing.get())["text"])
                        if message["type"] == "execution_resync":
                            assert message["discard_transient"] and message["reason"] == "overflow"
                            break
            assert app.state.products.streams.subscriptions == 0
        finally:
            gate.set()
