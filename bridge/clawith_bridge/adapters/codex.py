"""Codex CLI adapter — spawns `codex exec --json` and streams NDJSON events.

Uses OpenAI's Codex CLI (github.com/openai/codex, npm `@openai/codex`) in
non-interactive mode. Auth is inherited from `~/.codex/auth.json` (populated
by `codex login`). ChatGPT subscription OAuth works; API key also works; if
both exist, ChatGPT subscription wins (upstream quirk #2733/#3286).

Event stream from `codex exec --json` (authoritative source:
`codex-rs/exec/src/exec_events.rs`, cross-checked against v0.123.0 npm output):

    {"type":"thread.started","thread_id":"<uuid>"}
    {"type":"turn.started"}
    {"type":"item.started","item":{"id":"item_0","type":"command_execution", ...}}
    {"type":"item.updated","item":{...}}
    {"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"..."}}
    {"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,"output_tokens":N}}
    {"type":"turn.failed","error":{"message":"..."}}
    {"type":"error","message":"..."}

`item.type` values observed: agent_message, reasoning, command_execution,
mcp_tool_call, file_change, web_search, todo_list, error.

Bridge-context flag defaults (always on):
  --json --skip-git-repo-check --ephemeral [-C <cwd>]

Sandbox / approval selection (in order of precedence):
  1. params.dangerously_bypass → --dangerously-bypass-approvals-and-sandbox
  2. params.sandbox            → -s read-only|workspace-write|danger-full-access
  3. default                   → --full-auto  (workspace-write + no approvals)

Per-prompt `params`:
  - model               → `-m <model>`
  - sandbox             → `-s <level>` (see above)
  - dangerously_bypass  → danger flag (see above)
  - extra_args          → appended raw (list or shell-split string)
"""
from __future__ import annotations

import json
import shlex
from typing import Any

from .acp_base import npm_global_candidates, resolve_stdio_executable
from .base import SessionEvent, SubprocessAdapter


_STDIN_NOTICE = "Reading additional input from stdin..."


class CodexAdapter(SubprocessAdapter):
    name = "codex"
    capabilities = {
        "interactive_input": False,
        "cancellation": True,
        "tool_calls": True,
    }

    DEFAULT_EXECUTABLE = "codex"

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._finals: dict[str, list[str]] = {}

    def build_command(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> tuple[list[str], bytes | None]:
        configured = getattr(self.config, "executable", None) if self.config else None
        exe_prefix = resolve_stdio_executable(
            configured,
            self.DEFAULT_EXECUTABLE,
            npm_global_candidates(self.DEFAULT_EXECUTABLE),
        )
        argv: list[str] = [
            *exe_prefix, "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        if cwd:
            argv.extend(["-C", cwd])

        if params.get("dangerously_bypass"):
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        elif (sandbox := params.get("sandbox")):
            if sandbox in ("read-only", "workspace-write", "danger-full-access"):
                argv.extend(["-s", str(sandbox)])
            else:
                argv.append("--full-auto")
        else:
            argv.append("--full-auto")

        model = params.get("model")
        if model:
            argv.extend(["-m", str(model)])

        extra_args = params.get("extra_args")
        if isinstance(extra_args, list):
            argv.extend(str(a) for a in extra_args)
        elif isinstance(extra_args, str) and extra_args.strip():
            argv.extend(shlex.split(extra_args))

        argv.append(prompt)
        return argv, None

    def _finals_list(self) -> list[str]:
        return self._finals.setdefault("__current__", [])

    def parse_stdout_line(self, line: str) -> list[SessionEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return [SessionEvent(kind="stdout_chunk", payload={"text": line})]

        etype = evt.get("type")

        if etype == "thread.started":
            return [SessionEvent(kind="status", payload={
                "state": "init",
                "thread_id": evt.get("thread_id"),
            })]

        if etype == "turn.started":
            return []

        if etype == "turn.completed":
            usage = evt.get("usage") or {}
            return [SessionEvent(kind="status", payload={
                "state": "done",
                "exit_code": 0,
                "usage": usage,
            })]

        if etype == "turn.failed":
            err = (evt.get("error") or {}).get("message", "")
            return [SessionEvent(kind="status", payload={
                "state": "done",
                "exit_code": 1,
                "error": err,
            })]

        if etype == "error":
            return [SessionEvent(kind="stderr_chunk", payload={
                "text": str(evt.get("message", "")),
            })]

        if etype in ("item.started", "item.updated", "item.completed"):
            return self._parse_item(etype, evt.get("item") or {})

        return [SessionEvent(kind="stdout_chunk", payload={"text": line})]

    def _parse_item(self, etype: str, item: dict[str, Any]) -> list[SessionEvent]:
        itype = item.get("type")
        item_id = item.get("id")
        completed = etype == "item.completed"

        if itype == "agent_message":
            if completed:
                text = item.get("text", "") or ""
                if text:
                    self._finals_list().append(text)
                return [SessionEvent(kind="assistant_text", payload={"text": text})]
            return []

        if itype == "reasoning":
            if completed:
                return [SessionEvent(kind="thinking", payload={
                    "text": item.get("text", "") or "",
                })]
            return []

        if itype == "command_execution":
            if etype == "item.started":
                return [SessionEvent(kind="tool_call_start", payload={
                    "name": "shell",
                    "tool_use_id": item_id,
                    "args": {"command": item.get("command", "")},
                })]
            if completed:
                exit_code = item.get("exit_code")
                is_error = (
                    item.get("status") == "failed"
                    or (exit_code is not None and exit_code != 0)
                )
                return [SessionEvent(kind="tool_call_result", payload={
                    "tool_use_id": item_id,
                    "result": item.get("aggregated_output", "") or "",
                    "is_error": is_error,
                })]
            return []

        if itype == "mcp_tool_call":
            if etype == "item.started":
                server = item.get("server", "")
                tool = item.get("tool", "")
                name = f"{server}.{tool}" if server and tool else (tool or server or "mcp")
                return [SessionEvent(kind="tool_call_start", payload={
                    "name": name,
                    "tool_use_id": item_id,
                    "args": item.get("arguments") or {},
                })]
            if completed:
                error = item.get("error") or {}
                is_error = bool(error)
                result_text = (
                    error.get("message", "") if is_error
                    else _serialize_mcp_result(item.get("result") or {})
                )
                return [SessionEvent(kind="tool_call_result", payload={
                    "tool_use_id": item_id,
                    "result": result_text,
                    "is_error": is_error,
                })]
            return []

        if itype == "file_change":
            if completed:
                return [SessionEvent(kind="tool_call_result", payload={
                    "tool_use_id": item_id,
                    "result": _serialize_file_changes(item.get("changes") or []),
                    "is_error": item.get("status") == "failed",
                })]
            return []

        if itype == "web_search":
            if completed:
                return [SessionEvent(kind="tool_call_result", payload={
                    "tool_use_id": item_id,
                    "result": f"web_search: {item.get('query', '')}",
                    "is_error": False,
                })]
            return []

        if itype == "todo_list":
            return [SessionEvent(kind="status", payload={
                "state": "plan",
                "items": item.get("items") or [],
            })]

        if itype == "error":
            return [SessionEvent(kind="stderr_chunk", payload={
                "text": str(item.get("message", "")),
            })]

        return []

    def parse_stderr_line(self, line: str) -> list[SessionEvent]:
        line = line.rstrip()
        if not line:
            return []
        if _STDIN_NOTICE in line:
            return []
        return [SessionEvent(kind="stderr_chunk", payload={"text": line})]

    async def final_text(self, session_id: str) -> str:
        chunks = self._finals.pop("__current__", [])
        return "".join(chunks)


def _serialize_mcp_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content") or ""
                if t:
                    parts.append(str(t))
            else:
                parts.append(str(block))
        joined = "\n".join(parts)
        if joined:
            return joined
    structured = result.get("structured_content")
    if structured is not None:
        try:
            return json.dumps(structured, ensure_ascii=False)
        except Exception:
            pass
    return ""


def _serialize_file_changes(changes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in changes:
        if not isinstance(c, dict):
            continue
        lines.append(f"[{c.get('kind', '?')}] {c.get('path', '?')}")
    return "\n".join(lines)
