"""Tests for CodexAdapter.

Two layers:
  - Unit: parse_stdout_line / parse_stderr_line / build_command against
    crafted inputs — exercises the event mapping without spinning up a
    real codex subprocess.
  - Integration: spawn a python subprocess that emits canonical Codex
    NDJSON on stdout, read the event stream end-to-end, assert on
    observed events and the adapter's accumulated final_text.
"""
from __future__ import annotations

import json
import sys
import textwrap

import pytest

from clawith_bridge.adapters.codex import CodexAdapter
from clawith_bridge.config import AdapterConfig


def _make_adapter(config: AdapterConfig | None = None) -> CodexAdapter:
    return CodexAdapter(config=config)


# ── Unit: parse_stdout_line on crafted events ───────────────────────────


def test_parse_thread_started_yields_init_status():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "thread.started",
        "thread_id": "019db91e-ad71-7e51-ac83-84abb1a13d88",
    }))
    assert len(events) == 1
    assert events[0].kind == "status"
    assert events[0].payload["state"] == "init"
    assert events[0].payload["thread_id"] == "019db91e-ad71-7e51-ac83-84abb1a13d88"


def test_parse_turn_started_is_dropped():
    a = _make_adapter()
    assert a.parse_stdout_line(json.dumps({"type": "turn.started"})) == []


def test_parse_turn_completed_yields_done_with_usage():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10},
    }))
    assert len(events) == 1
    assert events[0].kind == "status"
    assert events[0].payload["state"] == "done"
    assert events[0].payload["exit_code"] == 0
    assert events[0].payload["usage"]["input_tokens"] == 100


def test_parse_turn_failed_yields_done_with_error():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "turn.failed",
        "error": {"message": "rate limited"},
    }))
    assert events[0].kind == "status"
    assert events[0].payload["state"] == "done"
    assert events[0].payload["exit_code"] == 1
    assert events[0].payload["error"] == "rate limited"


def test_parse_top_level_error_yields_stderr_chunk():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "error",
        "message": "fatal stream error",
    }))
    assert events[0].kind == "stderr_chunk"
    assert "fatal stream" in events[0].payload["text"]


def test_parse_agent_message_completed_yields_assistant_text_and_final():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "Hello, world"},
    }))
    assert len(events) == 1
    assert events[0].kind == "assistant_text"
    assert events[0].payload["text"] == "Hello, world"


def test_parse_agent_message_started_is_dropped():
    a = _make_adapter()
    assert a.parse_stdout_line(json.dumps({
        "type": "item.started",
        "item": {"id": "item_0", "type": "agent_message", "text": ""},
    })) == []


def test_parse_reasoning_completed_yields_thinking():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "r_0", "type": "reasoning", "text": "Let me think..."},
    }))
    assert len(events) == 1
    assert events[0].kind == "thinking"
    assert events[0].payload["text"] == "Let me think..."


def test_parse_reasoning_started_is_dropped():
    a = _make_adapter()
    assert a.parse_stdout_line(json.dumps({
        "type": "item.started",
        "item": {"id": "r_0", "type": "reasoning"},
    })) == []


def test_parse_command_execution_started_yields_tool_call_start():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.started",
        "item": {
            "id": "cmd_0",
            "type": "command_execution",
            "command": "ls -la",
        },
    }))
    assert len(events) == 1
    assert events[0].kind == "tool_call_start"
    assert events[0].payload["name"] == "shell"
    assert events[0].payload["tool_use_id"] == "cmd_0"
    assert events[0].payload["args"]["command"] == "ls -la"


def test_parse_command_execution_completed_success_is_not_error():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "cmd_0",
            "type": "command_execution",
            "aggregated_output": "total 4\ndrwxr-xr-x ...",
            "exit_code": 0,
            "status": "completed",
        },
    }))
    assert events[0].kind == "tool_call_result"
    assert events[0].payload["is_error"] is False
    assert "total 4" in events[0].payload["result"]


def test_parse_command_execution_nonzero_exit_is_error():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "cmd_0",
            "type": "command_execution",
            "aggregated_output": "not found",
            "exit_code": 127,
            "status": "completed",
        },
    }))
    assert events[0].payload["is_error"] is True


def test_parse_command_execution_status_failed_is_error():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "cmd_0",
            "type": "command_execution",
            "aggregated_output": "",
            "status": "failed",
        },
    }))
    assert events[0].payload["is_error"] is True


def test_parse_mcp_tool_call_started_uses_server_dot_tool_name():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.started",
        "item": {
            "id": "mcp_0",
            "type": "mcp_tool_call",
            "server": "filesystem",
            "tool": "read_file",
            "arguments": {"path": "/tmp/x"},
        },
    }))
    assert events[0].kind == "tool_call_start"
    assert events[0].payload["name"] == "filesystem.read_file"
    assert events[0].payload["args"] == {"path": "/tmp/x"}


def test_parse_mcp_tool_call_completed_with_error_is_error():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "mcp_0",
            "type": "mcp_tool_call",
            "server": "fs",
            "tool": "read_file",
            "error": {"message": "ENOENT"},
        },
    }))
    assert events[0].kind == "tool_call_result"
    assert events[0].payload["is_error"] is True
    assert "ENOENT" in events[0].payload["result"]


def test_parse_mcp_tool_call_completed_with_content_flattened():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "mcp_0",
            "type": "mcp_tool_call",
            "server": "fs",
            "tool": "read_file",
            "result": {
                "content": [
                    {"type": "text", "text": "line 1"},
                    {"type": "text", "text": "line 2"},
                ],
            },
        },
    }))
    assert events[0].kind == "tool_call_result"
    assert events[0].payload["is_error"] is False
    assert "line 1" in events[0].payload["result"]
    assert "line 2" in events[0].payload["result"]


def test_parse_file_change_completed_renders_summary():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "fc_0",
            "type": "file_change",
            "changes": [
                {"kind": "update", "path": "src/foo.py"},
                {"kind": "add", "path": "src/bar.py"},
            ],
            "status": "completed",
        },
    }))
    assert events[0].kind == "tool_call_result"
    assert "src/foo.py" in events[0].payload["result"]
    assert "src/bar.py" in events[0].payload["result"]
    assert events[0].payload["is_error"] is False


def test_parse_todo_list_emits_plan_status():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({
        "type": "item.updated",
        "item": {
            "id": "todo_0",
            "type": "todo_list",
            "items": [
                {"text": "read file", "completed": True},
                {"text": "write tests", "completed": False},
            ],
        },
    }))
    assert events[0].kind == "status"
    assert events[0].payload["state"] == "plan"
    assert len(events[0].payload["items"]) == 2


def test_parse_unknown_type_is_stdout_chunk():
    a = _make_adapter()
    events = a.parse_stdout_line(json.dumps({"type": "future.event", "foo": "bar"}))
    assert events[0].kind == "stdout_chunk"


def test_parse_unknown_item_type_is_dropped():
    a = _make_adapter()
    assert a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "x", "type": "unknown_future_item"},
    })) == []


def test_parse_malformed_json_is_stdout_chunk():
    a = _make_adapter()
    events = a.parse_stdout_line("not json at all")
    assert events[0].kind == "stdout_chunk"
    assert events[0].payload["text"] == "not json at all"


def test_parse_empty_line_is_noop():
    a = _make_adapter()
    assert a.parse_stdout_line("") == []
    assert a.parse_stdout_line("   \n") == []


# ── Unit: parse_stderr_line filters cosmetic noise ──────────────────────


def test_stderr_stdin_notice_is_filtered():
    a = _make_adapter()
    assert a.parse_stderr_line("Reading additional input from stdin...") == []


def test_stderr_other_lines_pass_through():
    a = _make_adapter()
    events = a.parse_stderr_line("Warning: model fallback")
    assert events[0].kind == "stderr_chunk"
    assert "fallback" in events[0].payload["text"]


def test_stderr_empty_is_noop():
    a = _make_adapter()
    assert a.parse_stderr_line("") == []


# ── Unit: build_command flag threading ──────────────────────────────────


def _argv_after_stub(adapter, prompt="hi", params=None, cwd=None, monkeypatch=None):
    # codex.py imports these symbols by name; patch at the use site.
    from clawith_bridge.adapters import codex as codex_mod
    if monkeypatch is not None:
        monkeypatch.setattr(
            codex_mod, "resolve_stdio_executable",
            lambda configured, default, paths: ["/fake/codex"],
        )
        monkeypatch.setattr(
            codex_mod, "npm_global_candidates",
            lambda name: [],
        )
    argv, stdin_bytes = adapter.build_command(prompt, params or {}, cwd)
    return argv, stdin_bytes


def test_build_command_minimal_has_required_flags(monkeypatch):
    adapter = _make_adapter()
    argv, stdin_bytes = _argv_after_stub(adapter, monkeypatch=monkeypatch)
    assert argv[:2] == ["/fake/codex", "exec"]
    assert "--json" in argv
    assert "--skip-git-repo-check" in argv
    assert "--ephemeral" in argv
    assert "--full-auto" in argv
    assert argv[-1] == "hi"
    assert stdin_bytes is None


def test_build_command_threads_cwd(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(adapter, cwd="/workspace/foo", monkeypatch=monkeypatch)
    assert "-C" in argv
    assert argv[argv.index("-C") + 1] == "/workspace/foo"


def test_build_command_threads_model(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(adapter, params={"model": "gpt-5.4"}, monkeypatch=monkeypatch)
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gpt-5.4"


def test_build_command_sandbox_replaces_full_auto(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(
        adapter, params={"sandbox": "read-only"}, monkeypatch=monkeypatch,
    )
    assert "--full-auto" not in argv
    assert "-s" in argv
    assert argv[argv.index("-s") + 1] == "read-only"


def test_build_command_invalid_sandbox_falls_back_to_full_auto(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(
        adapter, params={"sandbox": "nonsense"}, monkeypatch=monkeypatch,
    )
    assert "--full-auto" in argv
    assert "-s" not in argv


def test_build_command_dangerously_bypass_overrides_sandbox(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(
        adapter,
        params={"dangerously_bypass": True, "sandbox": "read-only"},
        monkeypatch=monkeypatch,
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "-s" not in argv
    assert "--full-auto" not in argv


def test_build_command_extra_args_list_appended(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(
        adapter, params={"extra_args": ["--color", "never"]}, monkeypatch=monkeypatch,
    )
    assert "--color" in argv
    assert argv[argv.index("--color") + 1] == "never"


def test_build_command_extra_args_string_shlex_split(monkeypatch):
    adapter = _make_adapter()
    argv, _ = _argv_after_stub(
        adapter, params={"extra_args": "--color never --output-last-message out.txt"},
        monkeypatch=monkeypatch,
    )
    assert "--color" in argv
    assert "--output-last-message" in argv


# ── Unit: final_text accumulation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_final_text_is_empty_before_any_events():
    a = _make_adapter()
    assert await a.final_text("s1") == ""


@pytest.mark.asyncio
async def test_final_text_captures_agent_message():
    a = _make_adapter()
    a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "Hello"},
    }))
    assert await a.final_text("s1") == "Hello"


@pytest.mark.asyncio
async def test_final_text_joins_multiple_agent_messages():
    a = _make_adapter()
    for text in ("first part. ", "second part."):
        a.parse_stdout_line(json.dumps({
            "type": "item.completed",
            "item": {"id": f"item_{text}", "type": "agent_message", "text": text},
        }))
    assert await a.final_text("s1") == "first part. second part."


@pytest.mark.asyncio
async def test_final_text_ignores_reasoning():
    a = _make_adapter()
    a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "r_0", "type": "reasoning", "text": "thinking..."},
    }))
    a.parse_stdout_line(json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "answer"},
    }))
    assert await a.final_text("s1") == "answer"


# ── Integration: fake python subprocess emits canonical NDJSON ──────────


FAKE_CODEX_SCRIPT = textwrap.dedent("""
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    # Reproduces the observed v0.123.0 event stream for a short prompt:
    send({"type": "thread.started", "thread_id": "019db91f-4963-7821-b78c-6f92b8bb961d"})
    send({"type": "turn.started"})
    send({"type": "item.completed", "item": {
        "id": "item_0", "type": "agent_message", "text": "Hello from fake codex"}})
    send({"type": "turn.completed", "usage": {
        "input_tokens": 1234, "cached_input_tokens": 1000, "output_tokens": 8}})
    # The real codex also emits this stderr noise — replicate so the stderr filter
    # has something realistic to drop.
    print("Reading additional input from stdin...", file=sys.stderr, flush=True)
""")


class _FakeCodexAdapter(CodexAdapter):
    """Spawn `python -c <fake codex>` instead of the real codex binary."""
    def __init__(self, script: str, config=None):
        super().__init__(config=config)
        self._script = script

    def build_command(self, prompt, params, cwd):
        return [sys.executable, "-u", "-c", self._script], None


@pytest.mark.asyncio
async def test_end_to_end_short_prompt_yields_events_and_final_text(tmp_path):
    adapter = _FakeCodexAdapter(FAKE_CODEX_SCRIPT)
    events = []
    async for ev in adapter.start_session(
        session_id="s-int",
        prompt="hi",
        params={},
        cwd=str(tmp_path),
        env={},
        timeout_s=30,
    ):
        events.append(ev)

    kinds = [e.kind for e in events]
    # init status, one assistant_text, done status. No stderr_chunk (noise filtered).
    assert "status" in kinds
    assert kinds.count("assistant_text") == 1
    assert "stderr_chunk" not in kinds

    init_status = next(e for e in events if e.kind == "status" and e.payload.get("state") == "init")
    assert init_status.payload["thread_id"] == "019db91f-4963-7821-b78c-6f92b8bb961d"

    assistant = next(e for e in events if e.kind == "assistant_text")
    assert assistant.payload["text"] == "Hello from fake codex"

    done_status = next(
        e for e in events if e.kind == "status" and e.payload.get("state") == "done"
    )
    assert done_status.payload["exit_code"] == 0
    assert done_status.payload["usage"]["input_tokens"] == 1234

    assert await adapter.final_text("s-int") == "Hello from fake codex"


FAKE_CODEX_TOOL_USE_SCRIPT = textwrap.dedent("""
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    send({"type": "thread.started", "thread_id": "t-tool"})
    send({"type": "turn.started"})
    send({"type": "item.completed", "item": {
        "id": "r_0", "type": "reasoning", "text": "I should list files."}})
    send({"type": "item.started", "item": {
        "id": "cmd_0", "type": "command_execution", "command": "ls"}})
    send({"type": "item.completed", "item": {
        "id": "cmd_0", "type": "command_execution",
        "command": "ls", "aggregated_output": "file1\\nfile2",
        "exit_code": 0, "status": "completed"}})
    send({"type": "item.completed", "item": {
        "id": "item_0", "type": "agent_message", "text": "Two files."}})
    send({"type": "turn.completed", "usage": {
        "input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 5}})
""")


@pytest.mark.asyncio
async def test_end_to_end_tool_use_stream(tmp_path):
    adapter = _FakeCodexAdapter(FAKE_CODEX_TOOL_USE_SCRIPT)
    events = []
    async for ev in adapter.start_session(
        session_id="s-tool",
        prompt="list files",
        params={},
        cwd=str(tmp_path),
        env={},
        timeout_s=30,
    ):
        events.append(ev)

    kinds = [e.kind for e in events]
    assert kinds.count("thinking") == 1
    assert kinds.count("tool_call_start") == 1
    assert kinds.count("tool_call_result") == 1
    assert kinds.count("assistant_text") == 1

    tc_start = next(e for e in events if e.kind == "tool_call_start")
    assert tc_start.payload["name"] == "shell"
    assert tc_start.payload["tool_use_id"] == "cmd_0"

    tc_result = next(e for e in events if e.kind == "tool_call_result")
    assert tc_result.payload["tool_use_id"] == "cmd_0"
    assert "file1" in tc_result.payload["result"]
    assert tc_result.payload["is_error"] is False

    assert await adapter.final_text("s-tool") == "Two files."


FAKE_CODEX_TURN_FAILED_SCRIPT = textwrap.dedent("""
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    send({"type": "thread.started", "thread_id": "t-fail"})
    send({"type": "turn.started"})
    send({"type": "turn.failed", "error": {"message": "rate limit exceeded"}})
""")


@pytest.mark.asyncio
async def test_end_to_end_turn_failed_surfaces_error_in_done_status(tmp_path):
    adapter = _FakeCodexAdapter(FAKE_CODEX_TURN_FAILED_SCRIPT)
    events = []
    async for ev in adapter.start_session(
        session_id="s-fail", prompt="x", params={},
        cwd=str(tmp_path), env={}, timeout_s=30,
    ):
        events.append(ev)

    done = next(
        e for e in events if e.kind == "status" and e.payload.get("state") == "done"
    )
    assert done.payload["exit_code"] == 1
    assert "rate limit" in done.payload["error"]
    # Empty final_text — no agent_message was ever emitted.
    assert await adapter.final_text("s-fail") == ""


@pytest.mark.asyncio
async def test_missing_executable_raises_file_not_found_or_chunk(tmp_path):
    class _MissingCodexAdapter(CodexAdapter):
        def build_command(self, prompt, params, cwd):
            return ["definitely-not-a-real-codex-binary-xyz-12345"], None

    adapter = _MissingCodexAdapter()
    events = []
    async for ev in adapter.start_session(
        session_id="s-miss", prompt="x", params={},
        cwd=str(tmp_path), env={}, timeout_s=5,
    ):
        events.append(ev)
    # SubprocessAdapter's base path yields a stderr_chunk describing the miss.
    assert any(e.kind == "stderr_chunk" for e in events)
