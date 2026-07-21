"""D-020 typed execution boundary for execute_code_e2b."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_readiness,
)
from app.services.sandbox.base import ExecutionResult


VALID_E2B_CONFIG = {
    "sandbox_type": "e2b",
    "api_key": "e2b-secret",
    "default_timeout": 30,
    "max_timeout": 60,
}


class FakeE2BBackend:
    name = "e2b"
    client = object()

    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.execute_calls = 0

    async def execute(self, **kwargs):
        del kwargs
        self.execute_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def health_check(self):
        raise AssertionError("Runtime readiness must not health-ping E2B")

    def _format_result(self, result):
        return f"exit={result.exit_code}"


def execution_result(*, success: bool, exit_code: int) -> ExecutionResult:
    return ExecutionResult(
        success=success,
        stdout="ok" if exit_code == 0 else "",
        stderr="" if exit_code == 0 else "failed",
        exit_code=exit_code,
        duration_ms=1,
    )


def install_backend(monkeypatch, backend: FakeE2BBackend) -> None:
    from app import config as config_module
    from app.services.sandbox import registry

    def local_fallback_forbidden():
        raise AssertionError("execute_code_e2b must not load local fallback config")

    monkeypatch.setattr(config_module, "get_sandbox_config", local_fallback_forbidden)
    monkeypatch.setattr(registry, "get_sandbox_backend", lambda _config: backend)


def test_e2b_has_explicit_canonical_readiness_and_typed_workset() -> None:
    assert builtin_readiness("execute_code_e2b") == "e2b_configuration"
    assert "execute_code_e2b" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_e2b_resolver_hides_without_config_and_never_health_pings(
    monkeypatch,
) -> None:
    tool = builtin_model_definition("execute_code_e2b")

    async def assigned(_agent_id):
        return [tool]

    async def missing(_agent_id, _name):
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_get_tool_config", missing)

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []

    async def configured(_agent_id, _name):
        return dict(VALID_E2B_CONFIG)

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    assert [item["function"]["name"] for item in resolved] == [
        "execute_code_e2b"
    ]


@pytest.mark.asyncio
async def test_e2b_dispatcher_reuses_typed_temp_workspace_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeE2BBackend(
        result=execution_result(success=True, exit_code=0)
    )
    install_backend(monkeypatch, backend)

    async def configured(_agent_id, _name):
        return dict(VALID_E2B_CONFIG)

    async def tenant(_agent_id):
        return "tenant"

    async def temp_path(agent_id, tenant_id, operation, **kwargs):
        assert tenant_id == "tenant"
        assert kwargs["sync_back"] is True
        assert kwargs["sync_back_on_non_success"] is True
        return await operation(tmp_path)

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant)
    monkeypatch.setattr(
        agent_tools,
        "_run_with_temp_workspace_outcome",
        temp_path,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        "execute_code_e2b",
        {"language": "python", "code": "print('ok')"},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert backend.execute_calls == 1


@pytest.mark.asyncio
async def test_e2b_nonzero_exit_is_failed(monkeypatch, tmp_path: Path) -> None:
    backend = FakeE2BBackend(
        result=execution_result(success=True, exit_code=7)
    )
    install_backend(monkeypatch, backend)

    async def configured(_agent_id, _name):
        return dict(VALID_E2B_CONFIG)

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "raise SystemExit(7)"},
        tool_name="execute_code_e2b",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "sandbox_execution_failed"
    assert outcome.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"language": "ruby", "code": "puts 'no'"},
        {"language": "python", "code": "print('no')", "timeout": 0},
    ],
)
async def test_e2b_argument_validation_is_failed(
    tmp_path: Path,
    arguments: dict,
) -> None:
    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        arguments,
        tool_name="execute_code_e2b",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_e2b_pre_dispatch_failure_is_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class MissingSdkBackend(FakeE2BBackend):
        @property
        def client(self):
            raise ImportError("e2b SDK missing")

    backend = MissingSdkBackend()
    install_backend(monkeypatch, backend)

    async def configured(_agent_id, _name):
        return dict(VALID_E2B_CONFIG)

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('never dispatched')"},
        tool_name="execute_code_e2b",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "sandbox_provider_unavailable"
    assert backend.execute_calls == 0


@pytest.mark.asyncio
async def test_e2b_post_dispatch_timeout_is_unknown_and_not_retried(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeE2BBackend(error=TimeoutError("response lost"))
    install_backend(monkeypatch, backend)

    async def configured(_agent_id, _name):
        return dict(VALID_E2B_CONFIG)

    async def fallback_forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("unknown E2B execution must not run locally")

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(
        agent_tools,
        "_execute_code_legacy_outcome",
        fallback_forbidden,
    )
    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('maybe ran')"},
        tool_name="execute_code_e2b",
    )

    assert outcome.status == "unknown"
    assert outcome.error_code == "sandbox_execution_outcome_unknown"
    assert outcome.retryable is False
    assert backend.execute_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {},
        {"sandbox_type": "subprocess", "api_key": "e2b-secret"},
        {"sandbox_type": "e2b", "api_key": ""},
    ],
)
async def test_e2b_invalid_config_fails_without_local_fallback(
    monkeypatch,
    tmp_path: Path,
    config: dict,
) -> None:
    from app import config as config_module

    async def configured(_agent_id, _name):
        return dict(config)

    async def fallback_forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("invalid E2B config must not execute locally")

    def local_config_forbidden():
        raise AssertionError("invalid E2B config must not load local fallback")

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(
        config_module,
        "get_sandbox_config",
        local_config_forbidden,
    )
    monkeypatch.setattr(
        agent_tools,
        "_execute_code_legacy_outcome",
        fallback_forbidden,
    )
    outcome = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('never local')"},
        tool_name="execute_code_e2b",
    )

    assert outcome.status == "failed"
    assert outcome.error_code in {
        "sandbox_configuration_missing",
        "sandbox_configuration_invalid",
    }


@pytest.mark.asyncio
async def test_e2b_backend_does_not_collapse_timeout_into_known_failure(
    monkeypatch,
) -> None:
    from app.services.sandbox.api import e2b_backend
    from app.services.sandbox.api.e2b_backend import E2bBackend
    from app.services.sandbox.config import SandboxConfig

    class AsyncSandbox:
        @classmethod
        async def create(cls, **kwargs):
            del kwargs
            raise TimeoutError("remote response lost")

    monkeypatch.setattr(
        e2b_backend,
        "_e2b",
        type("FakeE2B", (), {"AsyncSandbox": AsyncSandbox}),
    )
    backend = E2bBackend(SandboxConfig(type="e2b", api_key="configured"))

    with pytest.raises(TimeoutError):
        await backend.execute("print('maybe')", "python")
