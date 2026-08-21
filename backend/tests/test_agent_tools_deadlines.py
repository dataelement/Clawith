"""Operation-specific Tool deadline and cancellation contracts."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import agent_tools, agentbay_client
from app.services.agent_runtime.tool_contracts import (
    deadline_policy_for_tool,
    resolve_tool_deadline_seconds,
    tool_cancel_capability,
)


def test_deadline_precedence_is_explicit_then_default_capped_by_policy() -> None:
    assert resolve_tool_deadline_seconds("network_read") == 60
    assert resolve_tool_deadline_seconds("network_read", 12) == 12
    assert resolve_tool_deadline_seconds("network_read", 120) == 60
    assert deadline_policy_for_tool("read_emails").name == "network_read"
    assert deadline_policy_for_tool("execute_code").name == "local_code"
    assert resolve_tool_deadline_seconds("local_code") == 390
    assert resolve_tool_deadline_seconds("local_code", 30) == 390
    assert resolve_tool_deadline_seconds("local_code", 300) == 510
    assert tool_cancel_capability("local_code") == "cooperative"
    assert tool_cancel_capability("agentbay_code") == "stop_waiting_only"


def test_model_facing_code_timeout_matches_current_sandbox_bounds() -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds.",
                    }
                },
            },
        },
    }

    patched = agent_tools._with_code_timeout_schema(
        tool,
        default_timeout=180,
        max_timeout=300,
    )

    timeout_schema = patched["function"]["parameters"]["properties"]["timeout"]
    assert timeout_schema["default"] == 180
    assert timeout_schema["minimum"] == 180
    assert timeout_schema["maximum"] == 300
    assert "180" in timeout_schema["description"]
    assert "300" in timeout_schema["description"]
    assert "default" not in tool["function"]["parameters"]["properties"]["timeout"]


def test_code_sandbox_defaults_allow_longer_bounded_execution(monkeypatch) -> None:
    from app.config import Settings
    from app.services.sandbox.config import SandboxConfig

    monkeypatch.delenv("SANDBOX_DEFAULT_TIMEOUT", raising=False)
    monkeypatch.delenv("SANDBOX_MAX_TIMEOUT", raising=False)
    sandbox = SandboxConfig()
    settings = Settings(_env_file=None)

    assert sandbox.default_timeout == 180
    assert sandbox.max_timeout == 300
    assert settings.SANDBOX_DEFAULT_TIMEOUT == 180
    assert settings.SANDBOX_MAX_TIMEOUT == 300


@pytest.mark.asyncio
async def test_public_dns_resolution_uses_a_bounded_deadline(monkeypatch) -> None:
    observed: list[float | None] = []

    async def expire(awaitable, *, timeout=None):
        observed.append(timeout)
        awaitable.cancel()
        raise TimeoutError

    monkeypatch.setattr(agent_tools.asyncio, "wait_for", expire)

    normalized, error = await agent_tools._validate_public_http_url(
        "https://deadline.example.test/path"
    )

    assert normalized is None
    assert "Could not resolve hostname" in (error or "")
    assert observed == [agent_tools.PUBLIC_DNS_DEADLINE_SECONDS]


@pytest.mark.asyncio
async def test_imap_read_uses_a_bounded_operation_deadline(monkeypatch) -> None:
    observed: list[float | None] = []

    async def email_config(_agent_id):
        return {}

    def resolve_config(_stored):
        return (
            {
                "imap_host": "imap.example.test",
                "imap_port": 993,
                "email_address": "agent@example.test",
                "auth_code": "redacted",
            },
            frozenset({"imap"}),
        )

    async def expire(awaitable, *, timeout=None):
        observed.append(timeout)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(agent_tools, "_get_email_config", email_config)
    monkeypatch.setattr(
        agent_tools,
        "_resolve_local_email_configuration",
        resolve_config,
    )
    monkeypatch.setattr(agent_tools.asyncio, "wait_for", expire)

    outcome = await agent_tools._read_emails_outcome(uuid.uuid4(), {})

    assert outcome.status == "failed"
    assert outcome.error_code == "email_imap_deadline_exceeded"
    assert outcome.retryable is True
    assert observed == [agent_tools.EMAIL_IMAP_DEADLINE_SECONDS]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args", "timeout"),
    [
        ("code_execute", ("python", "print('ok')"), 7),
        ("code_read_file", ("/tmp/report.txt",), 11),
    ],
)
async def test_agentbay_code_operations_enforce_sdk_wait_deadline(
    monkeypatch,
    method: str,
    args: tuple[str, ...],
    timeout: int,
) -> None:
    client = object.__new__(agentbay_client.AgentBayClient)
    client._image_type = "code"
    client._session = SimpleNamespace(
        code=SimpleNamespace(run_code=lambda *_args: None),
        file_system=SimpleNamespace(read_file=lambda *_args: None),
    )
    observed: list[float | None] = []

    async def expire(awaitable, *, timeout=None):
        observed.append(timeout)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(agentbay_client.asyncio, "wait_for", expire)

    with pytest.raises(TimeoutError):
        await getattr(client, method)(*args, timeout=timeout)

    assert observed == [timeout]


@pytest.mark.asyncio
async def test_typed_agentbay_read_forwards_resolved_deadline(monkeypatch) -> None:
    observed: list[int] = []

    class Client:
        async def code_read_file(self, remote_path: str, timeout: int):
            assert remote_path == "/tmp/report.txt"
            observed.append(timeout)
            return SimpleNamespace(success=True, content="body")

    async def get_client(*_args, **_kwargs):
        return Client()

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        get_client,
    )

    outcome = await agent_tools._agentbay_read_outcome(
        "agentbay_code_read_file",
        uuid.uuid4(),
        {"remote_path": "/tmp/report.txt", "timeout": 120},
        session_id="session-1",
    )

    assert outcome.status == "succeeded"
    assert observed == [60]


@pytest.mark.asyncio
async def test_local_code_cancellation_terminates_child_and_cleans_script(
    tmp_path: Path,
) -> None:
    task = asyncio.create_task(
        agent_tools._execute_code_legacy_outcome(
            tmp_path,
            {
                "language": "python",
                "code": "import time\ntime.sleep(60)",
                "timeout": 60,
            },
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not (tmp_path / "_exec_tmp.py").exists()


@pytest.mark.asyncio
async def test_legacy_short_code_timeout_is_clamped_to_current_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    class Process:
        returncode = 0

        class Stream:
            async def read(self, _size):
                return b""

        stdout = Stream()
        stderr = Stream()

        async def wait(self):
            return 0

    async def create_process(*_args, **_kwargs):
        observed.update(_kwargs)
        return Process()

    async def wait_for(awaitable, timeout):
        observed["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "wait_for", wait_for)

    outcome = await agent_tools._execute_code_legacy_outcome(
        tmp_path,
        {"language": "python", "code": "print('ok')", "timeout": 30},
        default_timeout=180,
        max_timeout=300,
    )

    assert outcome.status == "succeeded"
    assert observed["timeout"] == 180


@pytest.mark.asyncio
async def test_short_code_timeout_is_clamped_before_sandbox_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app import config as config_module
    from app.services.sandbox import registry
    from app.services.sandbox.config import SandboxConfig

    observed: dict[str, object] = {}
    sandbox_config = SandboxConfig(default_timeout=180, max_timeout=300)

    class Backend:
        name = "subprocess"

        async def execute(self, **kwargs):
            observed.update(kwargs)
            return SimpleNamespace(success=True, exit_code=0, error=None)

        def _format_result(self, _result):
            return "ok"

    async def no_tool_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(config_module, "get_sandbox_config", lambda: sandbox_config)
    monkeypatch.setattr(agent_tools, "_get_tool_config", no_tool_config)
    monkeypatch.setattr(registry, "get_sandbox_backend", lambda _config: Backend())

    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('ok')", "timeout": 30},
    )

    assert outcome.status == "succeeded"
    assert observed["timeout"] == 180


@pytest.mark.asyncio
async def test_runtime_frozen_code_timeout_ignores_later_sandbox_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app import config as config_module
    from app.services.sandbox import registry
    from app.services.sandbox.config import SandboxConfig

    observed: dict[str, object] = {}
    later_config = SandboxConfig(default_timeout=600, max_timeout=900)

    class Backend:
        name = "subprocess"

        async def execute(self, **kwargs):
            observed.update(kwargs)
            return SimpleNamespace(success=True, exit_code=0, error=None)

        def _format_result(self, _result):
            return "ok"

    async def no_tool_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(config_module, "get_sandbox_config", lambda: later_config)
    monkeypatch.setattr(agent_tools, "_get_tool_config", no_tool_config)
    monkeypatch.setattr(registry, "get_sandbox_backend", lambda _config: Backend())

    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('ok')", "timeout": 30},
        runtime_code_timeout_seconds=180,
    )

    assert outcome.status == "succeeded"
    assert observed["timeout"] == 180


@pytest.mark.asyncio
async def test_builtin_dispatch_forwards_runtime_frozen_code_timeout(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    async def execute_code(**kwargs):
        observed.update(kwargs)
        return agent_tools._typed_success("done")

    monkeypatch.setattr(
        agent_tools,
        "_execute_code_with_workspace_outcome",
        execute_code,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        "execute_code",
        {"language": "python", "code": "print('ok')", "timeout": 30},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        runtime_tenant_id=str(uuid.uuid4()),
        runtime_code_timeout_seconds=180,
    )

    assert outcome.status == "succeeded"
    assert observed["runtime_code_timeout_seconds"] == 180
