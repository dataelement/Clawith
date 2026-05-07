"""OpenClaw adapter — talks to a local OpenClaw daemon via `openclaw acp`.

OpenClaw ships a first-class ACP (Agent Client Protocol) subcommand that runs
as a stdio JSON-RPC bridge and handles all the messy gateway plumbing
internally — Ed25519 device keypair, challenge signing, WebSocket v3 framing,
session routing, reconnection. We spawn it, speak stdio JSON-RPC, done.

Flags threaded through from `AdapterConfig.extra` (all optional):
  - url         → `--url <URL>`           gateway URL (defaults to local :18789)
  - token_file  → `--token-file <PATH>`   agent API key file
  - session_label → `--session-label <S>` human-readable new-session label
  - provenance  → `--provenance <STRING>`
  - verbose     → `--verbose`             flag; pass truthy value in TOML

Per-prompt `params` can also set `session_label` to override the config one.
"""
from __future__ import annotations

from typing import Any

from .acp_base import ACPSubprocessAdapter, npm_global_candidates, resolve_stdio_executable


class OpenClawAdapter(ACPSubprocessAdapter):
    name = "openclaw"
    capabilities = {
        "interactive_input": False,
        "cancellation": True,
        "tool_calls": True,
    }

    DEFAULT_EXECUTABLE = "openclaw"

    def build_acp_argv(self, params: dict[str, Any], cwd: str | None) -> list[str]:
        configured = getattr(self.config, "executable", None) if self.config else None
        exe_prefix = resolve_stdio_executable(
            configured,
            self.DEFAULT_EXECUTABLE,
            npm_global_candidates(self.DEFAULT_EXECUTABLE),
        )
        argv: list[str] = [*exe_prefix, "acp"]

        extra = (getattr(self.config, "extra", {}) if self.config else {}) or {}

        url = extra.get("url")
        if url:
            argv.extend(["--url", str(url)])

        token_file = extra.get("token_file")
        if token_file:
            argv.extend(["--token-file", str(token_file)])

        provenance = extra.get("provenance")
        if provenance:
            argv.extend(["--provenance", str(provenance)])

        session_label = params.get("session_label") or extra.get("session_label")
        if session_label:
            argv.extend(["--session-label", str(session_label)])

        if extra.get("verbose"):
            argv.append("--verbose")

        return argv
