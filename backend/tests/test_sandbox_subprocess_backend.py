"""Local sandbox bootstrap must not block the Backend event loop."""

import asyncio
from pathlib import Path

import pytest

from app.services.sandbox.config import SandboxConfig
from app.services.sandbox.local import subprocess_backend
from app.services.sandbox.local.subprocess_backend import SubprocessBackend


@pytest.mark.asyncio
async def test_workspace_venv_uses_async_subprocess(monkeypatch, tmp_path: Path) -> None:
    venv_path = tmp_path / ".venv"
    calls: list[tuple[object, ...]] = []

    class _Process:
        returncode = 0
        pid = 123

        async def communicate(self):
            await asyncio.sleep(0)
            (venv_path / "bin").mkdir(parents=True)
            (venv_path / "bin" / "python").write_text("", encoding="utf-8")
            return b"", b""

    async def fake_create(*args, **kwargs):
        calls.append((*args, kwargs))
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    backend = SubprocessBackend(SandboxConfig())

    await backend._ensure_workspace_venv(venv_path)

    assert calls
    assert calls[0][:3] == ("uv", "venv", "--seed")


@pytest.mark.asyncio
async def test_workspace_venv_timeout_terminates_child(monkeypatch, tmp_path: Path) -> None:
    terminated: list[int] = []

    class _Process:
        returncode = None
        pid = 456

        async def communicate(self):
            await asyncio.Event().wait()

        async def wait(self):
            self.returncode = -15
            return self.returncode

        def kill(self):
            self.returncode = -9

    async def fake_create(*_args, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(subprocess_backend, "VENV_CREATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(subprocess_backend.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        subprocess_backend.os,
        "killpg",
        lambda pid, _signal: terminated.append(pid),
    )
    backend = SubprocessBackend(SandboxConfig())

    with pytest.raises(RuntimeError, match="Timed out"):
        await backend._ensure_workspace_venv(tmp_path / ".venv")

    assert terminated == [456]


def test_subprocess_backend_proxy_env_propagation(tmp_path: Path) -> None:
    config = SandboxConfig(
        http_proxy="http://127.0.0.1:8080",
        https_proxy="http://127.0.0.1:8081",
        no_proxy="localhost,127.0.0.1",
    )
    backend = SubprocessBackend(config)
    env = backend._build_safe_env(tmp_path)

    assert env.get("http_proxy") == "http://127.0.0.1:8080"
    assert env.get("HTTP_PROXY") == "http://127.0.0.1:8080"
    assert env.get("https_proxy") == "http://127.0.0.1:8081"
    assert env.get("HTTPS_PROXY") == "http://127.0.0.1:8081"
    assert env.get("no_proxy") == "localhost,127.0.0.1"
    assert env.get("NO_PROXY") == "localhost,127.0.0.1"


def test_subprocess_backend_proxy_bwrap_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)
    config = SandboxConfig(
        http_proxy="http://proxy.example.com:8080",
        https_proxy="http://proxy.example.com:8443",
    )
    backend = SubprocessBackend(config)
    cmd = backend._build_bwrap_command(["python3", "-c", "print(1)"], tmp_path, tmp_path / ".venv")

    assert cmd is not None
    assert "--unshare-cgroup-try" in cmd
    assert "--unshare-cgroup" not in cmd
    assert "--unshare-user" not in cmd
    assert "--setenv" in cmd
    idx_http = cmd.index("http_proxy")
    assert cmd[idx_http + 1] == "http://proxy.example.com:8080"
    idx_https = cmd.index("https_proxy")
    assert cmd[idx_https + 1] == "http://proxy.example.com:8443"


def test_sandbox_config_proxy_parsing() -> None:
    data = {
        "http_proxy": "http://10.0.0.1:3128",
        "https_proxy": "http://10.0.0.1:3128",
        "no_proxy": ".local,10.0.0.0/8",
    }
    config = SandboxConfig.from_dict(data)
    assert config.http_proxy == "http://10.0.0.1:3128"
    assert config.https_proxy == "http://10.0.0.1:3128"
    assert config.no_proxy == ".local,10.0.0.0/8"

