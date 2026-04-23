"""ACP-over-stdio subprocess adapter base.

For adapters that speak Agent Client Protocol (ACP) through a local subprocess.
OpenClaw ships `openclaw acp` which does exactly this — spawns as stdio ACP,
handles gateway auth/device signing internally, streams session notifications.
Hermes will follow the same pattern in a later PR.

Wire protocol: JSON-RPC 2.0, one message per line, bidirectional.

Request/response flow on a single prompt turn:

    → initialize                              (client → agent)
    ← initialize result
    → session/new  (cwd + mcpServers)
    ← session/new result  (sessionId)
    → session/prompt  (sessionId + prompt: ContentBlock[])
    ← session/update notifications            (streaming; 0..N)
    ← session/prompt result  (stopReason)

The agent may also send requests back at us (e.g. `session/request_permission`,
`fs/read_text_file`). We handle the ones we care about and reject the rest so
the agent doesn't stall.

Why not `SubprocessAdapter`? That base writes `stdin_bytes` once and closes
stdin — fine for `claude -p`, wrong for stateful JSON-RPC. We manage the
process ourselves here.
"""
from __future__ import annotations

import abc
import asyncio
import glob
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar

from .base import BaseAdapter, SessionEvent


# ── Executable resolution (shared with claude_code.py's pattern) ────────

def resolve_stdio_executable(
    configured: str | None,
    default_name: str,
    well_known_paths: list[str],
) -> list[str]:
    """Return an argv prefix that reliably invokes a CLI that might live in a
    non-PATH location (npm global, launchd minimal PATH, systemd --user, etc.).

    Resolution order:
      1. configured (if not the bare default name)
      2. shutil.which(default_name)
      3. shutil.which(default_name + ".cmd") on Windows
      4. well_known_paths (first existing file)
      5. bare default_name — let PATH decide

    On Windows, .cmd/.bat paths are wrapped with `cmd.exe /c` because
    asyncio.create_subprocess_exec uses Win32 CreateProcess, which refuses
    .cmd directly.
    """
    if configured and configured != default_name:
        wrapped = _wrap_if_windows_cmd(configured)
        if wrapped:
            return wrapped

    found = shutil.which(default_name)
    if found:
        return _wrap_if_windows_cmd(found) or [found]

    if sys.platform == "win32":
        found = shutil.which(default_name + ".cmd")
        if found:
            return _wrap_if_windows_cmd(found) or [found]

    for candidate in well_known_paths:
        if os.path.exists(candidate):
            return _wrap_if_windows_cmd(candidate) or [candidate]

    return [default_name]


def _wrap_if_windows_cmd(path: str) -> list[str] | None:
    """Return [path], but wrap .cmd/.bat on Windows with cmd.exe /c.
    Return None if `path` doesn't exist."""
    if not path or not os.path.exists(path):
        return None
    if sys.platform == "win32" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", path]
    return [path]


def npm_global_candidates(name: str) -> list[str]:
    """Common npm-global install paths for a CLI named `name`."""
    paths: list[str] = []
    home = str(Path.home())
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(os.path.join(appdata, "npm", f"{name}.cmd"))
            paths.append(os.path.join(appdata, "npm", f"{name}.exe"))
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        paths.append(os.path.join(program_files, "nodejs", f"{name}.cmd"))
    elif sys.platform == "darwin":
        paths.extend([
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
            os.path.join(home, ".npm-global/bin", name),
            os.path.join(home, ".local/bin", name),
        ])
        paths.extend(sorted(
            glob.glob(os.path.join(home, f".nvm/versions/node/*/bin/{name}")),
            reverse=True,
        ))
    else:
        paths.extend([
            f"/usr/local/bin/{name}",
            f"/usr/bin/{name}",
            os.path.join(home, ".npm-global/bin", name),
            os.path.join(home, ".local/bin", name),
        ])
        paths.extend(sorted(
            glob.glob(os.path.join(home, f".nvm/versions/node/*/bin/{name}")),
            reverse=True,
        ))
    return paths


# ── ACPSubprocessAdapter ────────────────────────────────────────────────

class ACPSubprocessAdapter(BaseAdapter):
    """Base class for ACP-over-stdio adapters."""

    name: ClassVar[str] = "acp_base"
    capabilities: ClassVar[dict[str, Any]] = {
        "interactive_input": False,
        "cancellation": True,
        "tool_calls": True,
    }

    DEFAULT_EXECUTABLE: ClassVar[str] = "acp"
    KILL_GRACE_SEC: ClassVar[float] = 5.0
    ACP_PROTOCOL_VERSION: ClassVar[int] = 1

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._finals: dict[str, list[str]] = {}

    # ── Subclass hooks ──────────────────────────────────────────────────

    @abc.abstractmethod
    def build_acp_argv(self, params: dict[str, Any], cwd: str | None) -> list[str]:
        """Return argv whose tail is typically `<exe> acp [flags...]`."""

    def build_prompt_content(self, prompt: str) -> list[dict[str, Any]]:
        """Return ContentBlock[] for the prompt. Override for image/resource support."""
        return [{"type": "text", "text": prompt}]

    # ── start_session ───────────────────────────────────────────────────

    async def start_session(
        self,
        session_id: str,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],
        timeout_s: int,
    ) -> AsyncIterator[SessionEvent]:
        argv = self.build_acp_argv(params, cwd)
        effective_env = {**os.environ, **(env or {})}

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=effective_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            yield SessionEvent(kind="stderr_chunk", payload={"text": f"{argv[0]!r} not found: {e}"})
            raise
        self._procs[session_id] = proc
        self._finals[session_id] = []

        event_queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        next_id = [10]  # boxed counter; requests 1-3 reserved for protocol handshake

        async def _write_line(obj: dict[str, Any]) -> None:
            assert proc.stdin is not None
            if proc.stdin.is_closing():
                return
            payload = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            proc.stdin.write(payload)
            try:
                await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

        async def _request(req_id: int, method: str, params_: dict[str, Any]) -> dict[str, Any]:
            loop = asyncio.get_event_loop()
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            pending[req_id] = fut
            await _write_line({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params_,
            })
            return await fut

        async def _respond(req_id: int, result: dict[str, Any] | None = None,
                           error: dict[str, Any] | None = None) -> None:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
            if error is not None:
                msg["error"] = error
            else:
                msg["result"] = result or {}
            await _write_line(msg)

        async def _reader() -> None:
            """Read JSON-RPC from stdout; dispatch responses + notifications."""
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    await event_queue.put(SessionEvent(
                        kind="stdout_chunk", payload={"text": line},
                    ))
                    continue
                if not isinstance(msg, dict):
                    continue

                if "id" in msg and ("result" in msg or "error" in msg):
                    mid = msg.get("id")
                    fut = pending.pop(mid, None) if isinstance(mid, int) else None
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(
                                f"ACP error (id={mid}): {msg['error']}"
                            ))
                        else:
                            fut.set_result(msg.get("result") or {})
                    continue

                method = msg.get("method")
                if method == "session/update":
                    for ev in self._parse_session_update(
                        (msg.get("params") or {}), session_id,
                    ):
                        await event_queue.put(ev)
                elif method and "id" in msg:
                    # Agent is asking us for something. Handle a few common
                    # ones; reject the rest so the agent can continue.
                    await self._handle_agent_request(msg, _respond)
                # else: notification we don't care about — drop.
            await event_queue.put(None)  # EOF sentinel

        async def _stderr_reader() -> None:
            assert proc.stderr is not None
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    await event_queue.put(SessionEvent(
                        kind="stderr_chunk", payload={"text": text},
                    ))

        reader_task = asyncio.create_task(_reader())
        stderr_task = asyncio.create_task(_stderr_reader())

        prompt_task: asyncio.Task[dict[str, Any]] | None = None

        try:
            # 1. initialize
            init_result = await asyncio.wait_for(
                _request(1, "initialize", {
                    "protocolVersion": self.ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                }),
                timeout=min(30, timeout_s),
            )
            yield SessionEvent(kind="status", payload={
                "state": "init",
                "agent_capabilities": init_result.get("agentCapabilities") or {},
            })

            # 2. session/new
            session_result = await asyncio.wait_for(
                _request(2, "session/new", {
                    "cwd": os.path.abspath(cwd) if cwd else os.path.abspath(os.getcwd()),
                    "mcpServers": [],
                }),
                timeout=min(30, timeout_s),
            )
            acp_session_id = session_result.get("sessionId")
            if not isinstance(acp_session_id, str) or not acp_session_id:
                yield SessionEvent(kind="stderr_chunk", payload={
                    "text": "ACP session/new returned no sessionId",
                })
                raise RuntimeError("ACP session/new returned no sessionId")

            # 3. session/prompt — fire async, pump notifications in parallel
            prompt_task = asyncio.create_task(_request(3, "session/prompt", {
                "sessionId": acp_session_id,
                "prompt": self.build_prompt_content(prompt),
            }))

            start_t = asyncio.get_event_loop().time()
            while True:
                if prompt_task.done():
                    # Drain any events already queued before we noticed completion
                    while not event_queue.empty():
                        item = event_queue.get_nowait()
                        if item is None:
                            break
                        yield item
                    break
                remaining = timeout_s - (asyncio.get_event_loop().time() - start_t)
                if remaining <= 0:
                    yield SessionEvent(kind="stderr_chunk", payload={
                        "text": f"timeout after {timeout_s}s",
                    })
                    break
                try:
                    item = await asyncio.wait_for(
                        event_queue.get(), timeout=min(remaining, 0.5),
                    )
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    # stdout EOF — process exited mid-prompt
                    break
                yield item

            # Collect final stopReason
            try:
                prompt_result = await asyncio.wait_for(prompt_task, timeout=5)
                stop_reason = prompt_result.get("stopReason", "unknown")
                yield SessionEvent(kind="status", payload={
                    "state": "done",
                    "stop_reason": stop_reason,
                })
            except asyncio.TimeoutError:
                yield SessionEvent(kind="stderr_chunk", payload={
                    "text": "ACP prompt response not received after stream end",
                })
            except Exception as e:
                yield SessionEvent(kind="stderr_chunk", payload={
                    "text": f"ACP prompt failed: {e}",
                })
                raise
        finally:
            if prompt_task and not prompt_task.done():
                prompt_task.cancel()
            for fut in pending.values():
                if not fut.done():
                    fut.cancel()
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            reader_task.cancel()
            stderr_task.cancel()
            await self._terminate_proc(proc)
            self._procs.pop(session_id, None)

    # ── Notification parsing ────────────────────────────────────────────

    def _parse_session_update(
        self, params: dict[str, Any], session_id: str,
    ) -> list[SessionEvent]:
        update = params.get("update")
        if not isinstance(update, dict):
            return []
        kind = update.get("sessionUpdate")
        finals = self._finals.setdefault(session_id, [])

        if kind in ("agent_message_chunk", "user_message_chunk"):
            if kind == "user_message_chunk":
                return []  # echo of our own prompt — skip
            return self._text_events_from_content(
                update.get("content"), finals, event_kind="assistant_text",
            )
        if kind == "agent_thought_chunk":
            return self._text_events_from_content(
                update.get("content"), finals=None, event_kind="thinking",
            )
        if kind == "tool_call":
            return [SessionEvent(
                kind="tool_call_start",
                payload={
                    "name": update.get("title") or "",
                    "args": update.get("rawInput") or {},
                    "tool_use_id": update.get("toolCallId"),
                    "kind_hint": update.get("kind"),
                },
            )]
        if kind == "tool_call_update":
            status = update.get("status")
            # Only surface terminal states; intermediate updates are noise
            if status in ("completed", "failed"):
                return [SessionEvent(
                    kind="tool_call_result",
                    payload={
                        "tool_use_id": update.get("toolCallId"),
                        "result": self._serialize_tool_content(update.get("content")),
                        "is_error": status == "failed",
                    },
                )]
            return []
        if kind == "plan":
            return [SessionEvent(
                kind="status",
                payload={"state": "plan", "entries": update.get("entries") or []},
            )]
        # available_commands_update / current_mode_update / usage_update /
        # session_info_update / config_option_update — informational, drop.
        return []

    @staticmethod
    def _text_events_from_content(
        content: Any, finals: list[str] | None, event_kind: str,
    ) -> list[SessionEvent]:
        if not isinstance(content, dict):
            return []
        if content.get("type") != "text":
            return []
        text = content.get("text") or ""
        if not text:
            return []
        if finals is not None:
            finals.append(text)
        return [SessionEvent(kind=event_kind, payload={"text": text})]

    @staticmethod
    def _serialize_tool_content(content: Any) -> str:
        """Flatten ToolCallContent[] to a string for the Clawith tool_call_result."""
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "content":
                inner = block.get("content")
                if isinstance(inner, dict) and inner.get("type") == "text":
                    parts.append(inner.get("text") or "")
            elif btype == "diff":
                # Give the client enough to render; stringify structurally
                parts.append(json.dumps(
                    {"diff": block.get("path"), "oldText": block.get("oldText"),
                     "newText": block.get("newText")},
                    ensure_ascii=False,
                ))
            elif btype == "terminal":
                parts.append(f"[terminal {block.get('terminalId')}]")
        return "\n".join(p for p in parts if p)

    # ── Incoming-request handling ───────────────────────────────────────

    async def _handle_agent_request(self, msg: dict[str, Any], respond) -> None:
        """Agent → client requests. Auto-approve permissions; reject the rest."""
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "session/request_permission":
            # Default: pick the first "allow"-shaped option to let the agent proceed.
            options = params.get("options") or []
            chosen = None
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                opt_kind = str(opt.get("kind", "")).lower()
                if "allow" in opt_kind or "approve" in opt_kind:
                    chosen = opt
                    break
            if chosen is None and options and isinstance(options[0], dict):
                chosen = options[0]
            if chosen is not None:
                option_id = chosen.get("optionId") or chosen.get("id")
                await respond(req_id, result={
                    "outcome": {"outcome": "selected", "optionId": option_id},
                })
                return
            await respond(req_id, result={"outcome": {"outcome": "cancelled"}})
            return

        # Everything else — method not found. Agent should cope gracefully.
        await respond(req_id, error={
            "code": -32601,
            "message": f"Method not supported by clawith bridge: {method}",
        })

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def _terminate_proc(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.KILL_GRACE_SEC)
            return
        except asyncio.TimeoutError:
            pass
        except Exception:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass

    async def cancel(self, session_id: str, reason: str) -> None:
        proc = self._procs.get(session_id)
        if proc is None:
            return
        await self._terminate_proc(proc)

    async def final_text(self, session_id: str) -> str:
        return "".join(self._finals.pop(session_id, []))
