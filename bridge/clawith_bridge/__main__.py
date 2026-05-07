"""CLI entry point for clawith-bridge.

    clawith-bridge --server wss://clawith.example.com --token oc-xxxx
    clawith-bridge --config ~/.clawith-bridge.toml
    clawith-bridge install --server wss://... --token oc-xxxx --name "My Agent"

Config resolution is documented in `config.py`: TOML file < env < CLI flags.

The `install` subcommand is Windows-only and requires the PyInstaller-packaged
binary (it copies itself to %LOCALAPPDATA%\\Clawith\\bin\\ and registers a
user-scope scheduled task). On macOS/Linux, run the bridge directly or wrap it
with launchd/systemd yourself.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

from . import __version__
from .baked_config import read_baked_config
from .config import DEFAULT_CONFIG_PATH, load_config
from .connection import BridgeClient


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clawith-bridge", description="Clawith local-agent bridge")
    ap.add_argument("--server", help="Clawith server WS root (e.g. wss://host)")
    ap.add_argument("--token", help="Agent API key (oc-xxx)")
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to TOML config (default: {DEFAULT_CONFIG_PATH})",
    )
    ap.add_argument("--log-level", default="INFO", help="DEBUG | INFO | WARNING | ERROR")
    ap.add_argument("--version", action="version", version=f"clawith-bridge {__version__}")

    sub = ap.add_subparsers(dest="command")
    ip = sub.add_parser(
        "install",
        help="Windows-only: install as a user scheduled task that auto-starts at logon",
    )
    ip.add_argument("--server", required=True, help="Clawith server WS URL (wss://...)")
    ip.add_argument("--token", required=True, help="Agent API key (oc-xxx)")
    ip.add_argument("--name", default="", help="Agent display name (shown in install log only)")
    ip.add_argument(
        "--adapter",
        default="claude_code",
        choices=("claude_code", "openclaw", "hermes"),
        help="Which adapter to enable in the generated TOML (default: claude_code)",
    )

    return ap


def _hide_console_if_service() -> None:
    """When launched by Task Scheduler (no interactive TTY), hide our console window.

    Stays visible when user runs the exe manually from cmd/PowerShell.
    """
    if sys.platform != "win32":
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            return
    except (AttributeError, OSError):
        pass
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _run(args: argparse.Namespace) -> int:
    _hide_console_if_service()
    cfg = load_config(
        config_path=args.config,
        cli_server=args.server,
        cli_token=args.token,
    )

    if not cfg.token:
        logger.error("No agent token configured. Pass --token or set CLAWITH_BRIDGE_TOKEN.")
        return 2
    if not cfg.server:
        logger.error("No server URL configured. Pass --server or set CLAWITH_BRIDGE_SERVER.")
        return 2

    enabled = cfg.enabled_adapters()
    if not enabled:
        logger.error(
            "No adapters enabled. Enable at least one in the config file, e.g. "
            "[claude_code] enabled = true"
        )
        return 2
    logger.info(f"starting clawith-bridge {__version__}, adapters={enabled}, server={cfg.server}")

    client = BridgeClient(cfg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_):
        logger.info("shutdown requested")
        client.stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _shutdown)
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
    except (NotImplementedError, RuntimeError):
        # Windows asyncio doesn't support signal handlers in the selector loop
        pass

    try:
        loop.run_until_complete(client.run_forever())
    except KeyboardInterrupt:
        _shutdown()
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()

    return 0


def _maybe_auto_install(args: argparse.Namespace) -> int | None:
    """If this is a bare double-click of a setup.exe with baked config,
    run the install flow and return its exit code. Otherwise return None.

    Conditions:
      - no subcommand chosen
      - no explicit --server / --token on CLI
      - running from a PyInstaller-frozen binary
      - sys.executable has a config trailer
    """
    if sys.platform != "win32":
        return None
    if args.command is not None:
        return None
    if args.server or args.token:
        return None
    if not getattr(sys, "frozen", False):
        return None

    baked = read_baked_config()
    if baked is None:
        return None

    server = baked.get("server")
    token = baked.get("token")
    name = baked.get("agent_name") or baked.get("name") or ""
    adapter = baked.get("adapter") or "claude_code"
    if not server or not token:
        return None

    from .install_windows import install
    rc = install(server=server, token=token, name=name, adapter=adapter)

    # Double-clicking a console exe opens a window that closes on exit. Pause
    # so the user actually sees the install result.
    try:
        print()
        print("Press Enter to close this window...")
        input()
    except EOFError:
        pass
    return rc


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    if args.command == "install":
        from .install_windows import install
        return install(
            server=args.server,
            token=args.token,
            name=args.name,
            adapter=getattr(args, "adapter", "claude_code"),
        )

    auto_rc = _maybe_auto_install(args)
    if auto_rc is not None:
        return auto_rc

    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
