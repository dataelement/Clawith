"""Tests for OpenClawAdapter argv construction.

Focuses on build_acp_argv — the stdio JSON-RPC machinery is covered in
test_acp_adapter.py; here we verify the CLI flags we thread through to
`openclaw acp` match the user's config and per-request params.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from clawith_bridge.adapters import acp_base
from clawith_bridge.adapters.openclaw import OpenClawAdapter
from clawith_bridge.config import AdapterConfig


def _make_adapter(config: AdapterConfig | None = None) -> OpenClawAdapter:
    return OpenClawAdapter(config=config)


# ── build_acp_argv: flag threading ──────────────────────────────────────


def test_build_argv_no_extras_minimal(monkeypatch):
    # openclaw.py imports the symbol by name, so patching acp_base doesn't
    # reach the already-bound reference. Patch at the use site.
    from clawith_bridge.adapters import openclaw as openclaw_mod
    monkeypatch.setattr(
        openclaw_mod, "resolve_stdio_executable",
        lambda configured, default, paths: ["/fake/openclaw"],
    )
    argv = _make_adapter(AdapterConfig()).build_acp_argv({}, cwd=None)
    assert argv == ["/fake/openclaw", "acp"]


def test_build_argv_threads_url_and_token_file(monkeypatch):
    # openclaw.py imports the symbol by name, so patching acp_base doesn't
    # reach the already-bound reference. Patch at the use site.
    from clawith_bridge.adapters import openclaw as openclaw_mod
    monkeypatch.setattr(
        openclaw_mod, "resolve_stdio_executable",
        lambda configured, default, paths: ["/fake/openclaw"],
    )
    cfg = AdapterConfig(extra={
        "url": "https://gateway.example/ws",
        "token_file": "/secret/openclaw-token",
    })
    argv = _make_adapter(cfg).build_acp_argv({}, cwd=None)
    assert argv == [
        "/fake/openclaw", "acp",
        "--url", "https://gateway.example/ws",
        "--token-file", "/secret/openclaw-token",
    ]


def test_build_argv_params_session_label_overrides_config(monkeypatch):
    # openclaw.py imports the symbol by name, so patching acp_base doesn't
    # reach the already-bound reference. Patch at the use site.
    from clawith_bridge.adapters import openclaw as openclaw_mod
    monkeypatch.setattr(
        openclaw_mod, "resolve_stdio_executable",
        lambda configured, default, paths: ["/fake/openclaw"],
    )
    cfg = AdapterConfig(extra={"session_label": "config-label"})
    argv = _make_adapter(cfg).build_acp_argv(
        {"session_label": "param-label"}, cwd=None,
    )
    assert "--session-label" in argv
    # Param wins
    idx = argv.index("--session-label")
    assert argv[idx + 1] == "param-label"


def test_build_argv_verbose_adds_flag(monkeypatch):
    # openclaw.py imports the symbol by name, so patching acp_base doesn't
    # reach the already-bound reference. Patch at the use site.
    from clawith_bridge.adapters import openclaw as openclaw_mod
    monkeypatch.setattr(
        openclaw_mod, "resolve_stdio_executable",
        lambda configured, default, paths: ["/fake/openclaw"],
    )
    cfg = AdapterConfig(extra={"verbose": True})
    argv = _make_adapter(cfg).build_acp_argv({}, cwd=None)
    assert "--verbose" in argv


def test_build_argv_provenance(monkeypatch):
    # openclaw.py imports the symbol by name, so patching acp_base doesn't
    # reach the already-bound reference. Patch at the use site.
    from clawith_bridge.adapters import openclaw as openclaw_mod
    monkeypatch.setattr(
        openclaw_mod, "resolve_stdio_executable",
        lambda configured, default, paths: ["/fake/openclaw"],
    )
    cfg = AdapterConfig(extra={"provenance": "clawith-bridge/0.1"})
    argv = _make_adapter(cfg).build_acp_argv({}, cwd=None)
    assert "--provenance" in argv
    assert argv[argv.index("--provenance") + 1] == "clawith-bridge/0.1"


# ── Executable resolution: Windows .cmd shim ────────────────────────────


def test_wrap_if_windows_cmd_wraps_on_windows(tmp_path, monkeypatch):
    cmd_path = tmp_path / "openclaw.cmd"
    cmd_path.write_text("@echo off\n")
    monkeypatch.setattr(sys, "platform", "win32")
    result = acp_base._wrap_if_windows_cmd(str(cmd_path))
    assert result == ["cmd.exe", "/c", str(cmd_path)]


def test_wrap_if_windows_cmd_bare_exe_unchanged(tmp_path, monkeypatch):
    exe_path = tmp_path / "openclaw.exe"
    exe_path.write_text("")
    monkeypatch.setattr(sys, "platform", "win32")
    result = acp_base._wrap_if_windows_cmd(str(exe_path))
    assert result == [str(exe_path)]


def test_wrap_if_windows_cmd_returns_none_for_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert acp_base._wrap_if_windows_cmd(str(tmp_path / "nope.cmd")) is None


def test_resolve_falls_through_to_bare_name(monkeypatch):
    monkeypatch.setattr(acp_base.shutil, "which", lambda name: None)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    result = acp_base.resolve_stdio_executable(None, "openclaw", [])
    assert result == ["openclaw"]


def test_resolve_prefers_configured_path_when_exists(tmp_path, monkeypatch):
    fake = tmp_path / "custom-openclaw"
    fake.write_text("")
    # On POSIX the .cmd/.bat wrap is skipped, so resolve returns the raw path.
    monkeypatch.setattr(sys, "platform", "linux")
    result = acp_base.resolve_stdio_executable(str(fake), "openclaw", [])
    assert result == [str(fake)]


def test_resolve_uses_shutil_which_when_no_configured(monkeypatch):
    monkeypatch.setattr(acp_base.shutil, "which",
                        lambda name: "/usr/bin/openclaw" if name == "openclaw" else None)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    result = acp_base.resolve_stdio_executable(None, "openclaw", [])
    assert result == ["/usr/bin/openclaw"]


def test_npm_global_candidates_includes_cmd_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    candidates = acp_base.npm_global_candidates("openclaw")
    assert any(c.endswith("openclaw.cmd") for c in candidates)
    assert any(c.endswith("openclaw.exe") for c in candidates)
