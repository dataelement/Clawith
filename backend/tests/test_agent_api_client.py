from __future__ import annotations

import json
import sys
import uuid
import zipfile
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api.logs import get_agent_trace, read_agent_trace_entries
from tests.utils import agent_api_client as client_module
from tests.utils.agent_api_client import AgentApiClient


def _response(method: str, path: str, *, json_payload=None, content: bytes | None = None, status_code: int = 200):
    request = httpx.Request(method, f"http://test{path}")
    if content is not None:
        return httpx.Response(status_code, content=content, request=request)
    return httpx.Response(status_code, json=json_payload, request=request)


class RecordingHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, method, path, **kwargs):
        self.requests.append({"method": method, "path": path, **kwargs})
        if callable(self.responses[0]):
            return self.responses.pop(0)(method, path, **kwargs)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_agent_api_client_uses_jwt_and_existing_routes():
    http = RecordingHTTPClient([
        _response("GET", "/api/agents/", json_payload=[]),
        _response("POST", "/api/agents/agent-1/tasks/", json_payload={"id": "task-1"}),
        _response("GET", "/api/logs/agent-trace", json_payload=[{"event": "prompt"}]),
    ])
    client = AgentApiClient("http://test", "jwt-token", http_client=http)

    assert await client.list_agents() == []
    assert await client.create_async_run("agent-1", title="Run", prompt="Do it") == {"id": "task-1"}
    assert await client.get_run_trace(task_id="task-1") == [{"event": "prompt"}]

    assert [req["path"] for req in http.requests] == [
        "/api/agents/",
        "/api/agents/agent-1/tasks/",
        "/api/logs/agent-trace",
    ]
    assert all(req["headers"]["Authorization"] == "Bearer jwt-token" for req in http.requests)
    assert http.requests[1]["json"]["description"] == "Do it"
    assert http.requests[2]["params"]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_agent_api_client_get_run_status_filters_existing_tasks():
    http = RecordingHTTPClient([
        _response("GET", "/api/agents/agent-1/tasks/", json_payload=[
            {"id": "task-old", "status": "done"},
            {"id": "task-new", "status": "doing"},
        ]),
    ])
    client = AgentApiClient("http://test", "jwt-token", http_client=http)

    assert await client.get_run_status("agent-1", "task-new") == {"id": "task-new", "status": "doing"}


@pytest.mark.asyncio
async def test_agent_api_client_full_external_flow_creates_runs_and_collects_outputs():
    def route(method, path, **kwargs):
        params = kwargs.get("params") or {}
        payload = kwargs.get("json")
        if method == "POST" and path == "/api/agents/":
            assert payload["name"] == "Research Agent"
            assert payload["primary_model_id"] == "llm-primary"
            assert payload["fallback_model_id"] == "llm-fallback"
            assert payload["primary_model_id"] != payload["fallback_model_id"]
            assert payload["personality"] == "analytical and concise"
            assert payload["boundaries"] == "never expose secrets"
            assert payload["skill_ids"] == ["skill-analysis", "skill-reporting"]
            return _response(method, path, json_payload={"id": "agent-1", "name": payload["name"]})
        if method == "PUT" and path == "/api/tools/agents/agent-1":
            assert payload == [
                {"tool_id": "tool-read-file", "enabled": True},
                {"tool_id": "tool-write-file", "enabled": False},
            ]
            return _response(method, path, json_payload={"ok": True})
        if method == "PUT" and path == "/api/agents/agent-1/files/content" and params.get("path") == "soul.md":
            assert payload["content"] == "# Soul\nUse evidence first."
            return _response(method, path, json_payload={"status": "ok", "path": "soul.md"})
        if method == "PUT" and path == "/api/agents/agent-1/files/content" and params.get("path") == "memory/memory.md":
            assert payload["content"] == "Remember benchmark context."
            return _response(method, path, json_payload={"status": "ok", "path": "memory/memory.md"})
        if method == "POST" and path == "/api/agents/agent-1/tasks/":
            assert payload["title"] == "Run benchmark case"
            assert payload["description"] == "Complete the case and write output.txt"
            assert payload["eval_artifacts"] == {
                "type": "webarena_verified",
                "task_id": "case-1",
                "task_type": "NAVIGATE",
                "output_root": "/tmp/webarena",
            }
            return _response(method, path, json_payload={"id": "task-1", "status": "pending"})
        if method == "GET" and path == "/api/agents/agent-1/tasks/":
            return _response(method, path, json_payload=[
                {"id": "task-1", "status": "done", "title": "Run benchmark case"},
            ])
        if method == "GET" and path == "/api/agents/agent-1/tasks/task-1/logs":
            return _response(method, path, json_payload=[
                {"content": "started"},
                {"content": "completed"},
            ])
        if method == "GET" and path == "/api/logs/agent-trace":
            assert params["task_id"] == "task-1"
            return _response(method, path, json_payload=[
                {"event": "prompt", "messages": [{"role": "user", "content": "Complete the case"}]},
                {"event": "response", "content": "Done"},
            ])
        if method == "GET" and path == "/api/agents/agent-1/files/" and params.get("path") == "":
            return _response(method, path, json_payload=[
                {"path": "workspace/output.txt", "is_dir": False},
            ])
        if method == "GET" and path == "/api/agents/agent-1/files/download" and params.get("path") == "workspace/output.txt":
            return _response(method, path, content=b"benchmark result")
        raise AssertionError(f"unexpected request {method} {path} params={params} payload={payload}")

    http = RecordingHTTPClient([route] * 10)
    client = AgentApiClient("http://test", "jwt-token", http_client=http)

    created = await client.create_agent_from_spec({
        "agent": {
            "name": "Research Agent",
            "role_description": "Benchmark research operator",
            "primary_model_id": "llm-primary",
            "fallback_model_id": "llm-fallback",
            "permission_access_level": "manage",
        },
        "mind": {
            "personality": "analytical and concise",
            "boundaries": "never expose secrets",
            "soul": "# Soul\nUse evidence first.",
            "memory": "Remember benchmark context.",
        },
        "skills": {
            "skill_ids": ["skill-analysis", "skill-reporting"],
        },
        "tools": {
            "assignments": [
                {"tool_id": "tool-read-file", "enabled": True},
                {"tool_id": "tool-write-file", "enabled": False},
            ],
        },
    })
    collected = await client.run_and_collect(
        created["agent_id"],
        title="Run benchmark case",
        prompt="Complete the case and write output.txt",
        eval_artifacts={
            "type": "webarena_verified",
            "task_id": "case-1",
            "task_type": "NAVIGATE",
            "output_root": "/tmp/webarena",
        },
    )

    assert collected["status"]["status"] == "done"
    assert [entry["event"] for entry in collected["trace_logs"]] == ["prompt", "response"]
    assert [entry["content"] for entry in collected["task_logs"]] == ["started", "completed"]
    with zipfile.ZipFile(BytesIO(collected["workspace_zip"])) as archive:
        assert archive.read("workspace/output.txt") == b"benchmark result"


@pytest.mark.asyncio
async def test_agent_api_client_exports_workspace_as_zip():
    def route(method, path, **kwargs):
        params = kwargs.get("params") or {}
        if path.endswith("/files/") and params.get("path") == "":
            return _response(method, path, json_payload=[
                {"path": "workspace", "is_dir": True},
                {"path": "README.md", "is_dir": False},
            ])
        if path.endswith("/files/") and params.get("path") == "workspace":
            return _response(method, path, json_payload=[{"path": "workspace/report.md", "is_dir": False}])
        if path.endswith("/download") and params.get("path") == "README.md":
            return _response(method, path, content=b"hello")
        if path.endswith("/download") and params.get("path") == "workspace/report.md":
            return _response(method, path, content=b"report")
        raise AssertionError(f"unexpected request {method} {path} {params}")

    http = RecordingHTTPClient([route, route, route, route])
    client = AgentApiClient("http://test", "jwt-token", http_client=http)

    archive_bytes = await client.export_workspace("agent-1")
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        assert archive.read("README.md") == b"hello"
        assert archive.read("workspace/report.md") == b"report"


@pytest.mark.asyncio
async def test_agent_api_client_chat_sync_uses_websocket_jwt(monkeypatch):
    captured = {}

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self.messages = [
                {"type": "connected", "session_id": "session-1"},
                {"type": "done", "content": "welcome"},
                {"type": "chunk", "content": "answer"},
                {"type": "done", "content": "answer", "trace_id": "trace-1"},
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        async def recv(self):
            return json.dumps(self.messages.pop(0))

    def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        captured["ws"] = FakeWebSocket()
        return captured["ws"]

    monkeypatch.setattr(client_module.websockets, "connect", fake_connect)
    client = AgentApiClient("https://example.test", "jwt-token")

    result = await client.chat_sync("agent-1", "hello")

    assert result["reply"] == "answer"
    assert result["trace_id"] == "trace-1"
    assert captured["url"].startswith("wss://example.test/ws/chat/agent-1?")
    assert "token=jwt-token" in captured["url"]
    assert captured["ws"].sent == [{"content": "hello"}]


@pytest.mark.asyncio
async def test_agent_api_client_chat_sync_include_logs_fetches_trace(monkeypatch):
    captured = {}
    http = RecordingHTTPClient([
        _response("GET", "/api/logs/agent-trace", json_payload=[
            {"event": "prompt", "messages": [{"role": "user", "content": "hello"}]},
            {"event": "response", "content": "answer"},
        ]),
    ])

    class FakeWebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def send(self, payload):
            captured["sent"] = json.loads(payload)

        async def recv(self):
            return json.dumps({"type": "done", "content": "answer", "trace_id": "trace-1"})

    monkeypatch.setattr(client_module.websockets, "connect", lambda *_args, **_kwargs: FakeWebSocket())
    client = AgentApiClient("http://test", "jwt-token", http_client=http)

    result = await client.chat_sync("agent-1", "hello", include_logs=True)

    assert result["trace_id"] == "trace-1"
    assert [entry["event"] for entry in result["logs"]] == ["prompt", "response"]
    assert http.requests[0]["path"] == "/api/logs/agent-trace"
    assert http.requests[0]["params"]["trace_id"] == "trace-1"
    assert captured["sent"] == {"content": "hello"}


@pytest.mark.skipif(sys.version_info < (3, 10), reason="requires backend runtime imports")
@pytest.mark.asyncio
async def test_call_agent_llm_with_tools_logs_task_id(monkeypatch):
    from app.services.llm import caller

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeDB:
        def __init__(self, values):
            self.values = list(values)

        async def execute(self, _statement):
            return FakeResult(self.values.pop(0))

    class FakeClient:
        async def complete(self, **_kwargs):
            return SimpleNamespace(
                content="",
                reasoning_content=None,
                tool_calls=[
                    {
                        "id": "finish-1",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps({"content": "done"}),
                        },
                    }
                ],
                usage=None,
            )

        async def close(self):
            return None

    agent_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    model_id = uuid.uuid4()
    task_id = str(uuid.uuid4())
    logged = []

    fake_agent = SimpleNamespace(
        id=agent_id,
        name="Task Logger",
        creator_id=creator_id,
        primary_model_id=model_id,
        fallback_model_id=None,
    )
    fake_model = SimpleNamespace(
        provider="openai",
        model="gpt-test",
        base_url=None,
        temperature=None,
        max_output_tokens=None,
        request_timeout=None,
    )

    monkeypatch.setattr(caller, "_log_agent_loop", lambda event, **fields: logged.append((event, fields)))
    async def fake_get_agent_tools_for_llm(*_args, **_kwargs):
        return []

    monkeypatch.setattr(caller, "get_agent_tools_for_llm", fake_get_agent_tools_for_llm)
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(caller, "get_model_api_key", lambda _model: "test-key")
    monkeypatch.setattr(caller, "get_max_tokens", lambda *_args, **_kwargs: 16)

    async def fake_record_token_usage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(caller, "record_token_usage", fake_record_token_usage)

    reply = await caller.call_agent_llm_with_tools(
        db=FakeDB([fake_agent, fake_model]),
        agent_id=agent_id,
        system_prompt="system",
        user_prompt="user",
        max_rounds=1,
        session_id=task_id,
        task_id=task_id,
    )

    assert reply == "done"
    prompt_or_response = [fields for event, fields in logged if event in {"prompt", "response"}]
    assert prompt_or_response
    assert all(fields["task_id"] == task_id for fields in prompt_or_response)


def _log_line(timestamp: float, **extra) -> str:
    return json.dumps({
        "record": {
            "time": {"timestamp": timestamp, "repr": f"t-{timestamp}"},
            "level": {"name": "INFO"},
            "message": f"agent_loop {extra.get('event')}",
            "extra": extra,
        }
    })


def test_read_agent_trace_entries_filters_prompt_and_response(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("AGENT_TRACE_LOG_DIR", str(log_dir))
    (log_dir / "agent_trace.jsonl").write_text(
        "\n".join([
            _log_line(1, act="agent_loop", event="prompt", trace_id="trace-1", task_id="task-1", prompt="hi"),
            _log_line(2, act="agent_loop", event="response", trace_id="trace-1", task_id="task-1", response="ok"),
            _log_line(3, act="other", event="prompt", trace_id="trace-1"),
            _log_line(4, act="agent_loop", event="prompt", trace_id="trace-2"),
        ]),
        encoding="utf-8",
    )

    entries = read_agent_trace_entries(trace_id="trace-1")

    assert [entry["event"] for entry in entries] == ["prompt", "response"]
    assert entries[0]["prompt"] == "hi"
    assert entries[1]["response"] == "ok"


def test_read_agent_trace_entries_filters_task_agent_and_limit(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("AGENT_TRACE_LOG_DIR", str(log_dir))
    (log_dir / "agent_trace.jsonl").write_text(
        "\n".join([
            _log_line(1, act="agent_loop", event="prompt", trace_id="trace-1", task_id="task-1", agent_id="agent-1"),
            _log_line(2, act="agent_loop", event="response", trace_id="trace-1", task_id="task-1", agent_id="agent-1"),
            _log_line(3, act="agent_loop", event="tool_call", trace_id="trace-1", task_id="task-1", agent_id="agent-1"),
            _log_line(4, act="agent_loop", event="prompt", trace_id="trace-2", task_id="task-2", agent_id="agent-1"),
            _log_line(5, act="agent_loop", event="response", trace_id="trace-3", task_id="task-1", agent_id="agent-2"),
        ]),
        encoding="utf-8",
    )

    entries = read_agent_trace_entries(task_id="task-1", agent_id="agent-1", limit=2)

    assert [entry["event"] for entry in entries] == ["response", "tool_call"]
    assert all(entry["task_id"] == "task-1" for entry in entries)
    assert all(entry["agent_id"] == "agent-1" for entry in entries)


@pytest.mark.asyncio
async def test_get_agent_trace_requires_filter():
    with pytest.raises(HTTPException) as exc:
        await get_agent_trace(
            trace_id=None,
            task_id=None,
            agent_id=None,
            limit=200,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 422
