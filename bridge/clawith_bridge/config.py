"""Bridge configuration: CLI args + env + optional TOML file.

Resolution order (last wins):
  1. Defaults
  2. ~/.clawith-bridge.toml (if present)
  3. Environment variables (CLAWITH_BRIDGE_*)
  4. CLI flags

The token (agent API key) and server URL are the only required bits.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


DEFAULT_CONFIG_PATH = Path.home() / ".clawith-bridge.toml"


@dataclass
class AdapterConfig:
    """Per-adapter config knobs. Unknown keys are passed through as `extra`."""
    enabled: bool = True
    # Subprocess adapters
    executable: str | None = None
    default_cwd: str | None = None
    # Daemon adapters
    base_url: str | None = None
    auth_header: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BridgeConfig:
    server: str = "ws://127.0.0.1:8000"
    token: str = ""
    bridge_version: str = "clawith-bridge/0.1.0"
    max_concurrent_sessions: int = 4
    ping_interval: int = 25
    reconnect_min: float = 1.0
    reconnect_max: float = 60.0

    claude_code: AdapterConfig = field(default_factory=AdapterConfig)
    hermes: AdapterConfig = field(default_factory=lambda: AdapterConfig(enabled=False))
    openclaw: AdapterConfig = field(default_factory=lambda: AdapterConfig(enabled=False))

    def enabled_adapters(self) -> list[str]:
        out: list[str] = []
        if self.claude_code.enabled:
            out.append("claude_code")
        if self.hermes.enabled:
            out.append("hermes")
        if self.openclaw.enabled:
            out.append("openclaw")
        return out


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _apply_env(cfg: BridgeConfig) -> None:
    env = os.environ
    if v := env.get("CLAWITH_BRIDGE_SERVER"):
        cfg.server = v
    if v := env.get("CLAWITH_BRIDGE_TOKEN"):
        cfg.token = v
    if v := env.get("CLAWITH_BRIDGE_MAX_SESSIONS"):
        try:
            cfg.max_concurrent_sessions = int(v)
        except ValueError:
            pass
    # Adapter enable flags
    for name in ("claude_code", "hermes", "openclaw"):
        key = f"CLAWITH_BRIDGE_ADAPTER_{name.upper()}"
        v = env.get(key)
        if v is not None:
            getattr(cfg, name).enabled = v.strip().lower() not in ("0", "false", "no", "off")


def _apply_toml(cfg: BridgeConfig, data: dict[str, Any]) -> None:
    if not data:
        return
    for k in ("server", "token", "bridge_version"):
        if k in data:
            setattr(cfg, k, data[k])
    for k in ("max_concurrent_sessions", "ping_interval"):
        if k in data:
            setattr(cfg, k, int(data[k]))
    for k in ("reconnect_min", "reconnect_max"):
        if k in data:
            setattr(cfg, k, float(data[k]))
    for name in ("claude_code", "hermes", "openclaw"):
        section = data.get(name)
        if isinstance(section, dict):
            ac: AdapterConfig = getattr(cfg, name)
            for key in ("enabled",):
                if key in section:
                    setattr(ac, key, bool(section[key]))
            for key in ("executable", "default_cwd", "base_url", "auth_header"):
                if key in section:
                    setattr(ac, key, section[key])
            for k, v in section.items():
                if k not in {"enabled", "executable", "default_cwd", "base_url", "auth_header"}:
                    ac.extra[k] = v


def load_config(
    config_path: Path | None = None,
    cli_server: str | None = None,
    cli_token: str | None = None,
) -> BridgeConfig:
    cfg = BridgeConfig()
    path = config_path or DEFAULT_CONFIG_PATH
    _apply_toml(cfg, _load_toml(path))
    _apply_env(cfg)
    if cli_server:
        cfg.server = cli_server
    if cli_token:
        cfg.token = cli_token
    return cfg
