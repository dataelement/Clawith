"""Tests for ACPSubprocessAdapter.

Two layers:
  - Unit: _parse_session_update and _serialize_tool_content against crafted
    inputs — exercises the ACP notification → SessionEvent mapping without
    spinning up a process.
  - Integration: spawn a real Python subprocess that acts as a fake ACP agent,
    read the event stream end-to-end, assert on observed events and the
    adapter's accumulated final_text.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from clawith_bridge.adapters.acp_base import ACPSubprocessAdapter


# ── Unit tests for notification mapping ─────────────────────────────────


class _StubAdapter(ACPSubprocessAdapter):
    """Concrete subclass so we can call the helper methods (ACPSubprocessAdapter
    is abstract on build_acp_argv)."""
    def build_acp_argv(self, params, cwd):  # pragma: no cover — not called
        return ["unused"]


def _parse(update_dict):
    a = _StubAdapter()
    return a._parse_session_update({"update": update_dict}, session_id="s1")


def test_parse_agent_message_chunk_yields_assistant_text():
    events = _parse({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Hello"},
    })
    assert len(events) == 1
    assert events[0].kind == "assistant_text"
    assert events[0].payload == {"text": "Hello"}


def test_parse_user_message_chunk_is_dropped():
    # Echo of our own prompt — the bridge doesn't want to forward this.
    events = _parse({
        "sessionUpdate": "user_message_chunk",
        "content": {"type": "text", "text": "my prompt"},
    })
    assert events == []


def test_parse_agent_thought_chunk_yields_thinking():
    events = _parse({
        "sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "pondering..."},
    })
    assert len(events) == 1
    assert events[0].kind == "thinking"


def test_parse_tool_call_yields_tool_call_start():
    events = _parse({
        "sessionUpdate": "tool_call",
        "toolCallId": "tc-1",
        "title": "Read",
        "kind": "read",
        "rawInput": {"path": "/tmp/x"},
    })
    assert len(events) == 1
    assert events[0].kind == "tool_call_start"
    assert events[0].payload["name"] == "Read"
    assert events[0].payload["tool_use_id"] == "tc-1"
    assert events[0].payload["args"] == {"path": "/tmp/x"}


def test_parse_tool_call_update_completed_yields_tool_call_result():
    events = _parse({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc-1",
        "status": "completed",
        "content": [
            {"type": "content", "content": {"type": "text", "text": "file contents"}},
        ],
    })
    assert len(events) == 1
    assert events[0].kind == "tool_call_result"
    assert events[0].payload["result"] == "file contents"
    assert events[0].payload["is_error"] is False


def test_parse_tool_call_update_in_progress_is_dropped():
    # Only terminal statuses surface — intermediate updates are noise.
    events = _parse({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc-1",
        "status": "in_progress",
    })
    assert events == []


def test_parse_tool_call_update_failed_is_error():
    events = _parse({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc-2",
        "status": "failed",
        "content": [{"type": "content", "content": {"type": "text", "text": "boom"}}],
    })
    assert events[0].kind == "tool_call_result"
    assert events[0].payload["is_error"] is True


def test_parse_unknown_session_update_is_ignored():
    events = _parse({
        "sessionUpdate": "available_commands_update",
        "availableCommands": [],
    })
    assert events == []


@pytest.mark.asyncio
async def test_final_text_accumulates_agent_chunks_only():
    a = _StubAdapter()
    a._parse_session_update({"update": {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Hel"},
    }}, session_id="s1")
    a._parse_session_update({"update": {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "lo"},
    }}, session_id="s1")
    a._parse_session_update({"update": {
        "sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "thinking — not in final"},
    }}, session_id="s1")

    assert await a.final_text("s1") == "Hello"


def test_serialize_tool_content_flattens_mixed_blocks():
    out = ACPSubprocessAdapter._serialize_tool_content([
        {"type": "content", "content": {"type": "text", "text": "first"}},
        {"type": "diff", "path": "a.py", "oldText": "x", "newText": "y"},
        {"type": "content", "content": {"type": "text", "text": "last"}},
    ])
    assert "first" in out
    assert "last" in out
    assert "a.py" in out


# ── Integration test: real subprocess speaking fake ACP ─────────────────


FAKE_AGENT_SCRIPT = textwrap.dedent("""
    import json, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        mid = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"promptCapabilities": {}},
            }})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": "acp-sess-abc",
            }})
        elif method == "session/prompt":
            # Stream a few agent_message_chunks, then a tool_call, then terminate.
            for chunk in ("Hello, ", "world", "!"):
                send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "acp-sess-abc",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": chunk},
                    },
                }})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": "acp-sess-abc",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-42",
                    "title": "FakeTool",
                    "rawInput": {"q": 1},
                },
            }})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
            sys.exit(0)
""")


class _FakeACPAdapter(ACPSubprocessAdapter):
    """Spawn `python -c <fake agent>` instead of a real ACP binary."""
    def __init__(self, script: str):
        super().__init__(config=None)
        self._script = script

    def build_acp_argv(self, params, cwd):
        return [sys.executable, "-u", "-c", self._script]


@pytest.mark.asyncio
async def test_end_to_end_prompt_yields_events_and_final_text(tmp_path):
    adapter = _FakeACPAdapter(FAKE_AGENT_SCRIPT)
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
    # init status, 3 assistant_text chunks, 1 tool_call_start, done status.
    assert "status" in kinds
    assert kinds.count("assistant_text") == 3
    assert kinds.count("tool_call_start") == 1

    assistant_texts = [e.payload["text"] for e in events if e.kind == "assistant_text"]
    assert assistant_texts == ["Hello, ", "world", "!"]

    # final_text accumulates the three chunks
    final = await adapter.final_text("s-int")
    assert final == "Hello, world!"

    # Terminal status carries stop_reason
    done_status = next(
        (e for e in events if e.kind == "status" and e.payload.get("state") == "done"),
        None,
    )
    assert done_status is not None
    assert done_status.payload.get("stop_reason") == "end_turn"


@pytest.mark.asyncio
async def test_missing_executable_raises_file_not_found():
    class _MissingAdapter(ACPSubprocessAdapter):
        def build_acp_argv(self, params, cwd):
            return ["definitely-not-a-real-binary-xyz-12345"]

    adapter = _MissingAdapter()
    events = []
    with pytest.raises(FileNotFoundError):
        async for ev in adapter.start_session(
            session_id="s-miss", prompt="x", params={}, cwd=None, env={}, timeout_s=5,
        ):
            events.append(ev)
    # We want visibility before the raise — at least one stderr_chunk event.
    assert any(e.kind == "stderr_chunk" for e in events)
