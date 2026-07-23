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
