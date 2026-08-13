"""Contracts for Session-scoped sandbox policy and Redis execution leases."""

import uuid

import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.sandbox.config import SandboxConfig
from app.services.sandbox import execution_lease
from app.services.sandbox.execution_lease import SandboxExecutionLeaseStore
from app.services.sandbox.workspace_policy import (
    SandboxExecutionScope,
    build_workspace_policy,
    parse_canonical_uuid,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, _key_count, key, value, *args):
        if self.values.get(key) != value:
            return 0
        if "pexpire" in script:
            return 1
        del self.values[key]
        return 1


def test_isolated_policy_uses_exact_session_output() -> None:
    session_id = uuid.uuid4()
    policy = build_workspace_policy(
        mode="isolated_output",
        session_id=session_id,
        default_paths=["workspace", "memory", "skills"],
    )

    assert policy.publish_paths == (f"workspace/output/{session_id}",)
    assert policy.guest_output_path == f"/workspace/output/{session_id}"
    assert policy.materialized_paths == ("workspace", "memory", "skills")


def test_isolated_policy_requires_session() -> None:
    with pytest.raises(ValueError, match="requires a Session"):
        build_workspace_policy(mode="isolated_output", session_id=None, default_paths=["workspace"])


def test_isolated_output_prompt_directs_code_to_session_output_env() -> None:
    original = {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to execute"},
                },
            },
        },
    }

    patched = agent_tools._with_isolated_output_prompt(original)

    description = patched["function"]["description"]
    code_description = patched["function"]["parameters"]["properties"]["code"]["description"]
    for value in (description, code_description):
        assert "CLAWITH_SESSION_OUTPUT_DIR" in value
        assert "/workspace/output/<current-session-id>/" in value
        assert "workspace/<path> maps to /workspace/<path>" in value
        assert "Other sandbox writes are temporary" in value
    assert original["function"]["description"] == "Execute code."


@pytest.mark.asyncio
async def test_runtime_tools_apply_isolated_output_prompt(monkeypatch) -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute code.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
            },
        },
    }

    async def agent_tools_for_llm(_agent_id):
        return [tool]

    async def tool_config(_agent_id, tool_name):
        assert tool_name == "execute_code"
        return {"workspace_mode": "isolated_output"}

    async def no_dynamic_mcp(_agent_id):
        return set()

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", agent_tools_for_llm)
    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert len(resolved) == 1
    description = resolved[0]["function"]["description"]
    assert "CLAWITH_SESSION_OUTPUT_DIR" in description
    assert "/workspace/output/<current-session-id>/" in description


def test_session_uuid_must_be_canonical() -> None:
    value = uuid.uuid4()
    assert parse_canonical_uuid(str(value), label="session_id") == value
    with pytest.raises(ValueError, match="canonical UUID"):
        parse_canonical_uuid("not-a-session", label="session_id")


@pytest.mark.asyncio
async def test_execution_lease_is_tenant_scoped_and_owner_only(monkeypatch) -> None:
    redis = FakeRedis()

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(execution_lease, "get_redis", fake_get_redis)
    scope = SandboxExecutionScope(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    store = SandboxExecutionLeaseStore()

    first = await store.acquire(scope)
    second = await store.acquire(scope)

    assert first is not None
    assert second is None
    assert first.key.startswith(f"tenant:{scope.tenant_id}:sandbox-execution:")
    assert await first.ensure_publication_window(120) is True
    redis.values[first.key] = "foreign-owner"
    assert await first.ensure_publication_window(120) is False
    await first.release()
    assert redis.values[first.key] == "foreign-owner"


@pytest.mark.asyncio
async def test_local_session_busy_fails_before_code(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    executed = False

    async def tool_config(*_args):
        return {"workspace_mode": "isolated_output"}

    async def resolve_scope(**_kwargs):
        return SandboxExecutionScope(tenant_id, agent_id, session_id)

    async def busy(*_args, **_kwargs):
        return None

    async def forbidden_execute(*_args, **_kwargs):
        nonlocal executed
        executed = True
        return ToolExecutionOutcome("succeeded", "ok", None)

    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(agent_tools, "_resolve_sandbox_execution_scope", resolve_scope)
    monkeypatch.setattr(SandboxExecutionLeaseStore, "acquire", busy)
    monkeypatch.setattr(agent_tools, "_execute_code_outcome", forbidden_execute)
    monkeypatch.setattr(
        "app.config.get_sandbox_config",
        lambda: SandboxConfig(workspace_mode="merge"),
    )

    outcome = await agent_tools._execute_code_with_workspace_outcome(
        agent_id=agent_id,
        tenant_id=str(tenant_id),
        session_id=str(session_id),
        arguments={"language": "python", "code": "print(1)"},
        tool_name="execute_code",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "sandbox_session_busy"
    assert outcome.retryable is True
    assert executed is False


@pytest.mark.asyncio
async def test_invalid_session_scope_fails_before_lease(monkeypatch) -> None:
    acquired = False

    async def tool_config(*_args):
        return {"workspace_mode": "isolated_output"}

    async def invalid_scope(**_kwargs):
        raise ValueError("Session does not belong to the tenant and Agent")

    async def forbidden_acquire(*_args, **_kwargs):
        nonlocal acquired
        acquired = True

    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(agent_tools, "_resolve_sandbox_execution_scope", invalid_scope)
    monkeypatch.setattr(SandboxExecutionLeaseStore, "acquire", forbidden_acquire)
    monkeypatch.setattr("app.config.get_sandbox_config", lambda: SandboxConfig())

    outcome = await agent_tools._execute_code_with_workspace_outcome(
        agent_id=uuid.uuid4(),
        tenant_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        arguments={"language": "python", "code": "print(1)"},
        tool_name="execute_code",
    )

    assert outcome.error_code == "sandbox_execution_scope_invalid"
    assert acquired is False
