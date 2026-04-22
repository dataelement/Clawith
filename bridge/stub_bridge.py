"""Stub bridge for smoke-testing the Clawith local-agent session path.

What it does:
  1. Opens a WebSocket to `ws(s)://<server>/ws/bridge?token=<agent_api_key>`
  2. Waits for server `hello`
  3. Sends `bridge.register` advertising all three adapters
  4. On `session.start`: fakes a streaming session (thinking → a few chunks →
     one tool_call → session.done with a mock diff_summary)
  5. Responds to `ping` with `pong`; handles `session.cancel` gracefully

Run it like:

    python stub_bridge.py --server ws://127.0.0.1:8000 --token oc-<your-agent-key>

The agent must have `bridge_mode` set to `enabled` or `auto` (not the default
`disabled`) for the server to accept the connection.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from typing import Any

try:
    import websockets  # type: ignore
except ImportError:
    print("ERROR: install websockets first:  pip install websockets", file=sys.stderr)
    sys.exit(1)


PROTOCOL_VERSION = "1"
BRIDGE_VERSION = "stub-0.1"


async def _fake_session(ws: Any, session_id: str, prompt: str, adapter: str) -> None:
    """Emit a fake event stream for one session_id, then session.done."""
    async def event(kind: str, payload: dict) -> None:
        await ws.send(json.dumps({
            "type": "session.event",
            "session_id": session_id,
            "kind": kind,
            "payload": payload,
        }))

    # Accept
    await ws.send(json.dumps({
        "type": "session.accepted",
        "session_id": session_id,
        "adapter": adapter,
    }))

    # "Thinking"
    await event("thinking", {"text": f"(stub {adapter}) considering: {prompt[:80]!r}…"})
    await asyncio.sleep(0.3)

    # Stream a few chunks
    for piece in (
        f"Hi from the stub bridge (adapter={adapter}).\n",
        "I'm pretending to run a real local agent now.\n",
        f"Your prompt was: {prompt}\n",
    ):
        await event("assistant_text", {"text": piece})
        await asyncio.sleep(0.2)

    # Fake a tool_call round-trip
    await event("tool_call_start", {"name": "fake_tool", "args": {"x": 1}})
    await asyncio.sleep(0.2)
    await event("tool_call_result", {"name": "fake_tool", "result": "ok"})
    await asyncio.sleep(0.1)

    # Done with a mock diff_summary
    await ws.send(json.dumps({
        "type": "session.done",
        "session_id": session_id,
        "final_text": f"Done (stub {adapter}). I processed: {prompt[:200]}",
        "exit_code": 0,
        "stats": {"chunks": 3, "tool_calls": 1},
        "diff_summary": {
            "files_changed": 1,
            "insertions": 3,
            "deletions": 0,
            "files": [{"path": "stub/demo.txt", "+": 3, "-": 0}],
        },
    }))


async def run(server: str, token: str) -> None:
    url = f"{server.rstrip('/')}/ws/bridge?token={token}"
    print(f"[stub-bridge] connecting: {url}")
    async with websockets.connect(url, max_size=2 * 1024 * 1024) as ws:
        # Expect hello
        hello_raw = await ws.recv()
        hello = json.loads(hello_raw)
        print(f"[stub-bridge] server hello: {hello}")

        # Send bridge.register
        register = {
            "type": "bridge.register",
            "v": PROTOCOL_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "adapters": ["claude_code", "hermes", "openclaw"],
            "capabilities": {"interactive_input": False, "cancellation": True},
        }
        await ws.send(json.dumps(register))
        print("[stub-bridge] registered as claude_code/hermes/openclaw")

        active_sessions: dict[str, asyncio.Task] = {}

        async for raw in ws:
            try:
                frame = json.loads(raw)
            except Exception as e:
                print(f"[stub-bridge] bad frame: {e}")
                continue

            t = frame.get("type")
            if t == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue

            if t == "session.start":
                sid = frame["session_id"]
                prompt = frame.get("prompt", "")
                adapter = frame.get("adapter", "claude_code")
                print(f"[stub-bridge] session.start {sid} adapter={adapter} prompt={prompt!r}")
                task = asyncio.create_task(_fake_session(ws, sid, prompt, adapter))
                active_sessions[sid] = task
                task.add_done_callback(lambda _t, _sid=sid: active_sessions.pop(_sid, None))
                continue

            if t == "session.cancel":
                sid = frame["session_id"]
                task = active_sessions.pop(sid, None)
                if task:
                    task.cancel()
                await ws.send(json.dumps({
                    "type": "session.error",
                    "session_id": sid,
                    "error": f"cancelled: {frame.get('reason')}",
                }))
                continue

            if t == "session.input":
                # Stub doesn't support interactive input, just echo it back
                sid = frame["session_id"]
                await ws.send(json.dumps({
                    "type": "session.event",
                    "session_id": sid,
                    "kind": "assistant_text",
                    "payload": {"text": f"(stub echo of input) {frame.get('text','')}"},
                }))
                continue

            if t == "tool.response":
                # We never made a reverse call in this stub, but log if one arrives
                print(f"[stub-bridge] got tool.response (unexpected): {frame}")
                continue

            print(f"[stub-bridge] unhandled frame type={t!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://127.0.0.1:8000", help="Clawith server WS root")
    ap.add_argument("--token", required=True, help="Agent API key (oc-...)")
    args = ap.parse_args()

    loop = asyncio.new_event_loop()

    def _shutdown(*_):
        for t in asyncio.all_tasks(loop):
            t.cancel()

    try:
        loop.add_signal_handler(signal.SIGINT, _shutdown)
    except (NotImplementedError, RuntimeError):
        pass  # Windows

    try:
        loop.run_until_complete(run(args.server, args.token))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("[stub-bridge] bye")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
