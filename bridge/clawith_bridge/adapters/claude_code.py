"""Claude Code adapter.

Spawns `claude --output-format=stream-json --input-format=text -p <prompt>`
and parses newline-delimited JSON events from stdout.

Each stream-json event looks roughly like:
  {"type": "text", "text": "Hello"}
  {"type": "tool_use", "name": "Bash", "input": {...}}
  {"type": "tool_result", "tool_use_id": "...", "content": "..."}
  {"type": "assistant_message", "content": "...final..."}
  {"type": "result", "subtype": "success", "total_cost_usd": 0.0, ...}

We map these onto our EventKind taxonomy.
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from .base import SubprocessAdapter, SessionEvent


def resolve_claude_executable(configured: str | None) -> list[str]:
    """Return an argv prefix that reliably invokes the Claude Code CLI.

    Three platform gotchas this handles:

    1. **Windows .cmd shim**: `npm install -g @anthropic-ai/claude-code`
       produces `%APPDATA%\\npm\\claude.cmd` — a Node wrapper, not a .exe.
       `asyncio.create_subprocess_exec` uses Win32 CreateProcess, which
       refuses .cmd/.bat. We detect and wrap with `cmd.exe /c`.
    2. **macOS launchd**: user agents launched at login start with a bare
       PATH like `/usr/bin:/bin:/usr/sbin:/sbin` — no Homebrew, no npm global,
       no nvm. We explicitly probe the usual install locations.
    3. **Linux systemd --user**: same story as launchd.

    Resolution order: configured path → `shutil.which` → well-known locations
    → bare `claude` (let PATH decide — this works when the bridge is run
    interactively from a login shell).
    """
    if configured and configured != "claude":
        wrapped = _wrap_if_windows_cmd(configured)
        if wrapped:
            return wrapped

    found = shutil.which("claude")
    if found:
        return _wrap_if_windows_cmd(found) or [found]

    if sys.platform == "win32":
        found = shutil.which("claude.cmd")
        if found:
            return _wrap_if_windows_cmd(found) or [found]

    for candidate in _well_known_claude_paths():
        if os.path.exists(candidate):
            return _wrap_if_windows_cmd(candidate) or [candidate]

    return ["claude"]


def _wrap_if_windows_cmd(path: str) -> list[str] | None:
    """If `path` points at a real file, return [path] — but wrap .cmd/.bat
    on Windows with cmd.exe /c. Return None if `path` doesn't exist."""
    if not path or not os.path.exists(path):
        return None
    if sys.platform == "win32" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", path]
    return [path]


def _well_known_claude_paths() -> list[str]:
    """Platform-specific paths where `claude` is commonly installed but may
    be missing from the bridge process's PATH (launchd/systemd/Task Scheduler
    environments have minimal PATH)."""
    paths: list[str] = []
    home = str(Path.home())

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(os.path.join(appdata, "npm", "claude.cmd"))
            paths.append(os.path.join(appdata, "npm", "claude.exe"))
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        paths.append(os.path.join(program_files, "nodejs", "claude.cmd"))
    elif sys.platform == "darwin":
        paths.extend([
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            os.path.join(home, ".npm-global/bin/claude"),
            os.path.join(home, ".local/bin/claude"),
        ])
        paths.extend(sorted(glob.glob(os.path.join(home, ".nvm/versions/node/*/bin/claude")), reverse=True))
    else:
        paths.extend([
            "/usr/local/bin/claude",
            "/usr/bin/claude",
            os.path.join(home, ".npm-global/bin/claude"),
            os.path.join(home, ".local/bin/claude"),
        ])
        paths.extend(sorted(glob.glob(os.path.join(home, ".nvm/versions/node/*/bin/claude")), reverse=True))

    return paths


class ClaudeCodeAdapter(SubprocessAdapter):
    name = "claude_code"
    capabilities = {"interactive_input": False, "cancellation": True, "tool_calls": True}

    DEFAULT_EXECUTABLE = "claude"

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._finals: dict[str, list[str]] = {}

    def build_command(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> tuple[list[str], bytes | None]:
        configured = (getattr(self.config, "executable", None) if self.config else None)
        exe_prefix = resolve_claude_executable(configured)
        argv: list[str] = [*exe_prefix, "-p", prompt, "--output-format", "stream-json", "--verbose"]

        permission_mode = params.get("permission_mode")
        if permission_mode in ("acceptEdits", "bypassPermissions", "default", "plan"):
            argv.extend(["--permission-mode", permission_mode])

        model = params.get("model")
        if model:
            argv.extend(["--model", str(model)])

        allowed_tools = params.get("allowed_tools")
        if isinstance(allowed_tools, list) and allowed_tools:
            argv.extend(["--allowed-tools", ",".join(allowed_tools)])

        extra_args = params.get("extra_args")
        if isinstance(extra_args, list):
            argv.extend(str(a) for a in extra_args)
        elif isinstance(extra_args, str) and extra_args.strip():
            argv.extend(shlex.split(extra_args))

        return argv, None

    def _session_finals(self, session_id: str) -> list[str]:
        # Hacky: we don't get session_id threaded into parse_stdout_line, so we
        # track finals keyed by process. Only one concurrent session per process,
        # and the session manager binds us to one session at a time, so this is
        # fine if the adapter is instantiated per session — see session_manager.
        return self._finals.setdefault("__current__", [])

    def parse_stdout_line(self, line: str) -> list[SessionEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return [SessionEvent(kind="stdout_chunk", payload={"text": line})]

        kind = evt.get("type")
        subtype = evt.get("subtype")

        # Accumulator for the final answer
        finals = self._session_finals("__current__")

        if kind == "text":
            text = evt.get("text", "")
            if text:
                finals.append(text)
                return [SessionEvent(kind="assistant_text", payload={"text": text})]
            return []
        if kind == "thinking":
            text = evt.get("thinking") or evt.get("text") or ""
            return [SessionEvent(kind="thinking", payload={"text": text})]
        if kind == "tool_use":
            return [SessionEvent(
                kind="tool_call_start",
                payload={
                    "name": evt.get("name", ""),
                    "args": evt.get("input", {}),
                    "tool_use_id": evt.get("id") or evt.get("tool_use_id"),
                },
            )]
        if kind == "tool_result":
            content = evt.get("content")
            if isinstance(content, list):
                # Newer Claude Code emits content as a list of blocks.
                content_text = "\n".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            else:
                content_text = str(content) if content is not None else ""
            return [SessionEvent(
                kind="tool_call_result",
                payload={
                    "tool_use_id": evt.get("tool_use_id"),
                    "result": content_text,
                    "is_error": bool(evt.get("is_error")),
                },
            )]
        if kind == "assistant" and isinstance(evt.get("message"), dict):
            # The 2024+ format wraps content: {"message": {"content": [...]}}.
            msg = evt["message"]
            out: list[SessionEvent] = []
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    t = block.get("text", "")
                    if t:
                        finals.append(t)
                        out.append(SessionEvent(kind="assistant_text", payload={"text": t}))
                elif btype == "tool_use":
                    out.append(SessionEvent(
                        kind="tool_call_start",
                        payload={
                            "name": block.get("name", ""),
                            "args": block.get("input", {}),
                            "tool_use_id": block.get("id"),
                        },
                    ))
                elif btype == "thinking":
                    out.append(SessionEvent(
                        kind="thinking", payload={"text": block.get("thinking", "")},
                    ))
            return out
        if kind == "user" and isinstance(evt.get("message"), dict):
            # tool_result blocks inside a user turn
            msg = evt["message"]
            out: list[SessionEvent] = []
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content_text = "\n".join(
                            c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                        )
                    else:
                        content_text = str(content) if content is not None else ""
                    out.append(SessionEvent(
                        kind="tool_call_result",
                        payload={
                            "tool_use_id": block.get("tool_use_id"),
                            "result": content_text,
                            "is_error": bool(block.get("is_error")),
                        },
                    ))
            return out
        if kind == "result":
            # Terminal frame — carries totals
            stats_payload = {
                "state": "done",
                "exit_code": 0 if subtype == "success" else 1,
                "total_cost_usd": evt.get("total_cost_usd"),
                "duration_ms": evt.get("duration_ms"),
                "num_turns": evt.get("num_turns"),
            }
            return [SessionEvent(kind="status", payload=stats_payload)]
        if kind == "system":
            return [SessionEvent(kind="status", payload={"state": "init", **{k: v for k, v in evt.items() if k != "type"}})]

        # Unknown — pass through as stdout_chunk for visibility.
        return [SessionEvent(kind="stdout_chunk", payload={"text": line})]

    async def final_text(self, session_id: str) -> str:
        text = "".join(self._finals.pop("__current__", []))
        return text
