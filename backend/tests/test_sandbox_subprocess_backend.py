"""Local sandbox bootstrap must not block the Backend event loop."""

import asyncio
import signal
from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest

from app.services.sandbox.config import SandboxConfig
from app.services.sandbox.local import subprocess_backend
from app.services.sandbox.local.subprocess_backend import (
    SANDBOX_VENV_PATH,
    SubprocessBackend,
    close_subprocess_sandbox_run,
)


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


@pytest.mark.asyncio
async def test_terminate_and_reap_process_waits_after_group_termination(monkeypatch) -> None:
    terminated: list[tuple[int, signal.Signals]] = []

    class _Process:
        returncode = None
        pid = 789

        def __init__(self) -> None:
            self.wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            self.returncode = -signal.SIGTERM
            return self.returncode

        def kill(self) -> None:
            self.returncode = -signal.SIGKILL

    proc = _Process()
    monkeypatch.setattr(subprocess_backend.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        subprocess_backend.os,
        "killpg",
        lambda pid, sig: terminated.append((pid, sig)),
    )

    backend = SubprocessBackend(SandboxConfig())
    await backend._terminate_and_reap_process(proc)  # type: ignore[arg-type]

    assert terminated == [(789, signal.SIGTERM)]
    assert proc.wait_calls == 1


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


def test_bash_commands_enable_pipefail(tmp_path: Path) -> None:
    backend = SubprocessBackend(SandboxConfig())

    assert backend._build_command("bash", "/workspace/.tmp/test.sh") == [
        "bash", "--noprofile", "--norc", "-o", "pipefail", "/workspace/.tmp/test.sh",
    ]
    assert backend._build_host_command("bash", tmp_path / "test.sh", tmp_path) == [
        "bash", "--noprofile", "--norc", "-o", "pipefail", str(tmp_path / "test.sh"),
    ]


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


def test_isolated_bwrap_uses_workspace_tool_paths_and_writable_copy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)
    staging = tmp_path / "staging"
    (staging / ".tmp").mkdir(parents=True)
    session_id = uuid.uuid4()
    output_path = f"workspace/output/{session_id}"
    backend = SubprocessBackend(SandboxConfig(workspace_mode="isolated_output"))

    cmd = backend._build_bwrap_command(
        ["python", "/workspace/.tmp/_exec_tmp.py"],
        tmp_path,
        tmp_path / ".venv",
        staging_path=staging,
        writable_path=output_path,
    )

    assert cmd is not None
    root_index = cmd.index("/workspace")
    assert cmd[root_index - 2] == "--bind"
    assert cmd[root_index - 1] == str(staging / "workspace")
    skills_index = cmd.index("/skills")
    assert cmd[skills_index - 2] == "--bind"
    assert cmd[skills_index - 1] == str(staging / "skills")
    assert "/workspace/skills" not in cmd
    venv_index = cmd.index(SANDBOX_VENV_PATH)
    assert cmd[venv_index - 2] == "--ro-bind"
    assert cmd[venv_index - 1] == str(tmp_path / ".venv")
    assert "/workspace/.venv" not in cmd
    env_index = cmd.index("CLAWITH_SESSION_OUTPUT_DIR")
    assert cmd[env_index + 1] == f"/workspace/output/{session_id}"
    assert f"/workspace/{output_path}" not in cmd
    virtual_env_index = cmd.index("VIRTUAL_ENV")
    assert cmd[virtual_env_index + 1] == SANDBOX_VENV_PATH
    chdir_index = cmd.index("--chdir")
    assert cmd[chdir_index + 1] == "/"


@pytest.mark.asyncio
async def test_persistent_bwrap_session_is_reused_for_same_agent_loop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = SubprocessBackend(SandboxConfig(workspace_mode="isolated_output"))
    run_id = str(uuid.uuid4())
    agent_id = uuid.uuid4()
    session_id = str(uuid.uuid4())
    publish_paths = [f"workspace/output/{session_id}"]
    starts = 0
    persistent = SimpleNamespace(
        process=SimpleNamespace(returncode=None),
        agent_id=agent_id,
        session_id=session_id,
        workspace_mode="isolated_output",
        publish_paths=tuple(publish_paths),
        work_path=(tmp_path / "workspace").resolve(),
        staging_path=tmp_path / "persistent",
    )

    async def start(**_kwargs):
        nonlocal starts
        starts += 1
        SubprocessBackend._run_sessions[run_id] = persistent
        return persistent

    monkeypatch.setattr(backend, "_start_persistent_session", start)
    SubprocessBackend._run_sessions.pop(run_id, None)
    try:
        first = await backend._persistent_session(
            run_id=run_id,
            work_path=tmp_path / "workspace",
            venv_path=tmp_path / "venv",
            agent_id=agent_id,
            session_id=session_id,
            workspace_mode="isolated_output",
            publish_paths=publish_paths,
        )
        second = await backend._persistent_session(
            run_id=run_id,
            work_path=tmp_path / "workspace",
            venv_path=tmp_path / "venv",
            agent_id=agent_id,
            session_id=session_id,
            workspace_mode="isolated_output",
            publish_paths=publish_paths,
        )
    finally:
        SubprocessBackend._run_sessions.pop(run_id, None)

    assert first is persistent
    assert second is persistent
    assert starts == 1


@pytest.mark.asyncio
async def test_close_subprocess_sandbox_run_releases_process_and_workspace(monkeypatch) -> None:
    closed = []

    async def close_process(run_id):
        closed.append(("process", run_id))

    async def close_workspace(run_id):
        closed.append(("workspace", run_id))

    monkeypatch.setattr(SubprocessBackend, "close_run", close_process)
    monkeypatch.setattr(subprocess_backend, "close_run_workspace", close_workspace)

    await close_subprocess_sandbox_run("run-1")

    assert closed == [("process", "run-1"), ("workspace", "run-1")]


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


@pytest.mark.asyncio
async def test_sandbox_output_sanitization(tmp_path: Path) -> None:
    # Setup staging and target directories
    staging = tmp_path / "staging"
    staging.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    # 1. Create HTML file with malicious script tag
    html_file = staging / "index.html"
    html_file.write_text("<html><body><h1>Hello</h1><script>alert(1)</script></body></html>", encoding="utf-8")

    # 2. Create SVG file with malicious onload handler
    svg_file = staging / "image.svg"
    svg_file.write_text('<svg onload="alert(1)"></svg>', encoding="utf-8")

    # 3. Create a banned script file
    script_file = staging / "evil.sh"
    script_file.write_text("rm -rf /", encoding="utf-8")

    # Run verification and merge
    backend = SubprocessBackend(SandboxConfig())
    await backend._verify_and_merge_outputs(staging, target)

    # Assertions
    # HTML should be cleaned
    cleaned_html = (target / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in cleaned_html
    assert "alert(1)" not in cleaned_html

    # SVG should be cleaned
    cleaned_svg = (target / "image.svg").read_text(encoding="utf-8")
    assert "onload" not in cleaned_svg
    assert "alert(1)" not in cleaned_svg

    # Banned script should NOT be merged
    assert not (target / "evil.sh").exists()


@pytest.mark.asyncio
async def test_sandbox_quota_ignores_unchanged_materialized_files(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    target.mkdir()
    for index in range(150):
        relative = Path("skills") / f"existing-{index}.md"
        (staging / relative).parent.mkdir(parents=True, exist_ok=True)
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        (staging / relative).write_text("unchanged", encoding="utf-8")
        (target / relative).write_text("unchanged", encoding="utf-8")

    await SubprocessBackend(SandboxConfig())._verify_and_merge_outputs(
        staging,
        target,
    )

    assert len(list((target / "skills").iterdir())) == 150


@pytest.mark.asyncio
async def test_sandbox_quota_rejects_too_many_changed_files(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    target.mkdir()
    for index in range(101):
        (staging / f"generated-{index}.txt").write_text(
            "generated",
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match="too many changed files"):
        await SubprocessBackend(SandboxConfig())._verify_and_merge_outputs(
            staging,
            target,
        )


@pytest.mark.asyncio
async def test_sandbox_quota_rejects_too_many_deletions(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    target.mkdir()
    for index in range(101):
        (target / f"existing-{index}.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(RuntimeError, match="deleted too many files"):
        await SubprocessBackend(SandboxConfig())._verify_and_merge_outputs(
            staging,
            target,
        )


@pytest.mark.asyncio
async def test_isolated_output_skips_shared_workspace_file_count_limits(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    output_path = Path("workspace/output/session-1")
    staging_output = staging / output_path
    target_output = target / output_path
    staging_output.mkdir(parents=True)
    target_output.mkdir(parents=True)

    for index in range(120):
        (staging_output / f"generated-{index}.txt").write_text(
            "generated",
            encoding="utf-8",
        )
        (target_output / f"deleted-{index}.txt").write_text(
            "deleted",
            encoding="utf-8",
        )

    await SubprocessBackend(SandboxConfig())._verify_and_merge_outputs(
        staging,
        target,
        publish_paths=[output_path.as_posix()],
        workspace_mode="isolated_output",
    )

    assert len(list(target_output.glob("generated-*.txt"))) == 120
    assert not list(target_output.glob("deleted-*.txt"))


def test_sandbox_pip_hijack_shebang(tmp_path: Path) -> None:
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir(parents=True)

    # Create fake pip scripts
    pip_file = venv_bin / "pip"
    pip_file.write_text("original shebang", encoding="utf-8")

    backend = SubprocessBackend(SandboxConfig())
    backend._fix_pip_shebangs(tmp_path)

    # Check wrapper content
    content = pip_file.read_text(encoding="utf-8")
    assert "if [ -d /workspace/.tmp ]; then" in content
    assert "REQ_FILE=\"/workspace/.tmp/.pip_request_${REQ_ID}\"" in content
    assert "cat \"$OUT_FILE\"" in content


@pytest.mark.asyncio
async def test_sandbox_pip_watcher_loop(tmp_path: Path, monkeypatch) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "workspace" / ".tmp").mkdir(parents=True)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")

    # Mock subprocess run to avoid running actual uv pip in unit test
    uv_calls = []
    class FakeProc:
        returncode = 0
        async def communicate(self):
            return b"installed pandas\n", b""

    async def fake_create_subprocess(*args, **kwargs):
        uv_calls.append(args)
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)

    backend = SubprocessBackend(SandboxConfig())

    # Simulate a pip request being written inside the clone's .tmp/ folder
    req_file = staging / "workspace" / ".tmp" / ".pip_request_123"
    req_file.write_text("install pandas", encoding="utf-8")

    stop_event = asyncio.Event()

    # Start watcher as a background task
    watcher_task = asyncio.create_task(
        backend._watch_pip_requests(staging, venv, stop_event)
    )

    # Wait for watcher to process request and write response
    res_file = staging / "workspace" / ".tmp" / ".pip_response_123"
    for _ in range(20):
        if res_file.exists():
            break
        await asyncio.sleep(0.1)

    # Stop watcher
    stop_event.set()
    await watcher_task

    # Assertions
    assert res_file.exists()
    assert res_file.read_text(encoding="utf-8") == "0"
    output_file = staging / "workspace" / ".tmp" / ".pip_output_123"
    assert output_file.read_text(encoding="utf-8") == "installed pandas\n"
    assert not req_file.exists()
    assert uv_calls
    assert uv_calls[0] == (
        "uv", "pip", "install", "--python", str(venv / "bin" / "python"), "pandas",
    )


@pytest.mark.asyncio
async def test_sandbox_protected_system_files(tmp_path: Path) -> None:
    # Setup staging and target directories
    staging = tmp_path / "staging"
    staging.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    # Create target files
    (target / "soul.md").write_text("original soul content", encoding="utf-8")
    (target / "tasks.json").write_text("original tasks content", encoding="utf-8")

    # Clone real workspace to staging
    backend = SubprocessBackend(SandboxConfig())
    backend._clone_workspace_to_staging(target, staging)

    # Verify cloning worked
    assert (staging / "soul.md").read_text(encoding="utf-8") == "original soul content"

    # Simulate modification inside the sandbox
    (staging / "soul.md").write_text("modified malicious soul", encoding="utf-8")
    (staging / "tasks.json").write_text("modified tasks", encoding="utf-8")

    # Run verification and merge
    await backend._verify_and_merge_outputs(staging, target)

    # Assertions: protected files should NOT be updated in the real workspace
    assert (target / "soul.md").read_text(encoding="utf-8") == "original soul content"
    assert (target / "tasks.json").read_text(encoding="utf-8") == "original tasks content"


@pytest.mark.asyncio
async def test_sandbox_workspace_shadow_cloning(tmp_path: Path) -> None:
    # Setup staging and target directories
    staging = tmp_path / "staging"
    staging.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    # Create workspace files on host
    (target / "existing.txt").write_text("hello world", encoding="utf-8")
    (target / "folder").mkdir()
    (target / "folder" / "data.csv").write_text("1,2,3", encoding="utf-8")

    # Excluded files/folders shouldn't be cloned
    (target / ".venv").mkdir()
    (target / ".venv" / "bin").mkdir(parents=True)
    (target / ".venv" / "bin" / "pip").write_text("pip text", encoding="utf-8")
    (target / ".tmp").mkdir()
    (target / ".tmp" / "temp.log").write_text("logs", encoding="utf-8")

    # Clone real workspace to staging
    backend = SubprocessBackend(SandboxConfig())
    backend._clone_workspace_to_staging(target, staging)

    # Verify cloned files exist
    assert (staging / "existing.txt").exists()
    assert (staging / "folder" / "data.csv").read_text(encoding="utf-8") == "1,2,3"

    # Verify excluded files DO NOT exist in staging
    assert not (staging / ".venv").exists()
    assert not (staging / ".tmp").exists()

    # Simulate sandbox creating a new file, modifying an existing file, and deleting a file
    (staging / "new_file.txt").write_text("new content", encoding="utf-8")
    (staging / "existing.txt").write_text("modified hello world", encoding="utf-8")
    (staging / "folder" / "data.csv").unlink()

    # Run verification and merge
    await backend._verify_and_merge_outputs(staging, target)

    # Assertions: changes should be merged back
    assert (target / "new_file.txt").read_text(encoding="utf-8") == "new content"
    assert (target / "existing.txt").read_text(encoding="utf-8") == "modified hello world"
    assert not (target / "folder" / "data.csv").exists()
