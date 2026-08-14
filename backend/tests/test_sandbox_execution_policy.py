"""Contracts for Session-scoped sandbox policy and Redis execution leases."""

import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.sandbox.config import SandboxConfig
from app.services.sandbox import execution_lease
from app.services.sandbox.execution_lease import SandboxExecutionLeaseStore
from app.services.sandbox.local.run_workspace import close_run_workspace
from app.services.sandbox.run_scope import sandbox_run_scope_id
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
    assert policy.publication_conflict_mode == "overwrite"


def test_merge_policy_preserves_conflict_detection() -> None:
    policy = build_workspace_policy(
        mode="merge",
        session_id=uuid.uuid4(),
        default_paths=["workspace"],
    )

    assert policy.publication_conflict_mode == "fail"


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
        assert "skills/<path> maps to /skills/<path>" in value
        assert "memory/<path> maps to /memory/<path>" in value
        assert "working directory is /" in value
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
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", agent_tools_for_llm)
    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_bindings",
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
async def test_same_group_session_uses_distinct_agent_leases(monkeypatch) -> None:
    redis = FakeRedis()

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(execution_lease, "get_redis", fake_get_redis)
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    first_scope = SandboxExecutionScope(tenant_id, uuid.uuid4(), session_id)
    second_scope = SandboxExecutionScope(tenant_id, uuid.uuid4(), session_id)
    store = SandboxExecutionLeaseStore()

    first = await store.acquire(first_scope)
    second = await store.acquire(second_scope)

    assert first is not None
    assert second is not None
    assert first.key != second.key
    await first.release()
    await second.release()


def test_same_group_session_artifacts_remain_agent_scoped() -> None:
    session_id = uuid.uuid4()
    path = f"workspace/output/{session_id}/result.txt"
    first_agent = uuid.uuid4()
    second_agent = uuid.uuid4()

    first_ref = agent_tools._workspace_artifact_ref(first_agent, path)
    second_ref = agent_tools._workspace_artifact_ref(second_agent, path)

    assert first_ref == f"workspace://{first_agent}/{path}"
    assert second_ref == f"workspace://{second_agent}/{path}"
    assert first_ref != second_ref


@pytest.mark.asyncio
async def test_authorized_native_group_scope_executes_with_isolated_output(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    output_path = f"workspace/output/{session_id}/result.txt"
    calls = []

    class _Lease:
        ownership_lost = False

        async def start_heartbeat(self):
            return None

        async def ensure_publication_window(self, _seconds):
            return True

        async def release(self):
            return None

    async def tool_config(*_args):
        return {"workspace_mode": "isolated_output"}

    async def authorize(**kwargs):
        calls.append(("authorize", kwargs))
        return object()

    async def acquire(_self, scope, **_kwargs):
        calls.append(("lease", scope))
        return _Lease()

    async def prepare(*_args, **kwargs):
        calls.append(("materialize", kwargs))
        return SimpleNamespace(root=tmp_path, cleanup=lambda: None)

    async def execute(_agent_id, _root, _arguments, **kwargs):
        calls.append(("execute", kwargs))
        return ToolExecutionOutcome("succeeded", "ok", None)

    async def flush(*_args, **_kwargs):
        return {
            "updated": [output_path],
            "deleted": [],
            "conflicted": [],
            "skipped": [],
        }

    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(
        agent_tools.chat_session_dao,
        "get_active_for_sandbox_agent",
        authorize,
    )
    monkeypatch.setattr(SandboxExecutionLeaseStore, "acquire", acquire)
    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", prepare)
    monkeypatch.setattr(agent_tools, "_execute_code_outcome", execute)
    monkeypatch.setattr(agent_tools, "flush_temp_workspace", flush)
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

    assert outcome.status == "succeeded"
    assert outcome.artifact_refs == (f"workspace://{agent_id}/{output_path}",)
    assert [call[0] for call in calls] == ["authorize", "lease", "materialize", "execute"]
    assert calls[0][1] == {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "session_id": session_id,
    }
    assert calls[1][1] == SandboxExecutionScope(tenant_id, agent_id, session_id)
    assert calls[2][1]["publish_paths"] == [f"workspace/output/{session_id}"]
    assert calls[3][1]["session_id"] == str(session_id)
    assert calls[3][1]["publish_paths"] == [f"workspace/output/{session_id}"]


@pytest.mark.asyncio
async def test_scope_resolver_uses_sandbox_session_authorization(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    calls = []

    async def authorize(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(
        agent_tools.chat_session_dao,
        "get_active_for_sandbox_agent",
        authorize,
    )

    scope = await agent_tools._resolve_sandbox_execution_scope(
        tenant_id=str(tenant_id),
        agent_id=agent_id,
        session_id=str(session_id),
    )

    assert scope == SandboxExecutionScope(tenant_id, agent_id, session_id)
    assert calls == [
        {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "session_id": session_id,
        }
    ]


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
    materialized = False
    executed = False

    async def tool_config(*_args):
        return {"workspace_mode": "isolated_output"}

    async def invalid_scope(**_kwargs):
        raise ValueError("Session does not belong to the tenant and Agent")

    async def forbidden_acquire(*_args, **_kwargs):
        nonlocal acquired
        acquired = True

    async def forbidden_materialize(*_args, **_kwargs):
        nonlocal materialized
        materialized = True

    async def forbidden_execute(*_args, **_kwargs):
        nonlocal executed
        executed = True

    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(agent_tools, "_resolve_sandbox_execution_scope", invalid_scope)
    monkeypatch.setattr(SandboxExecutionLeaseStore, "acquire", forbidden_acquire)
    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", forbidden_materialize)
    monkeypatch.setattr(agent_tools, "_execute_code_outcome", forbidden_execute)
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
    assert materialized is False
    assert executed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("publication_owner", ["gateway", "workspace_cas"])
async def test_isolated_execution_uses_replacement_publication(
    monkeypatch,
    tmp_path,
    publication_owner,
) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    conflict_modes = []
    prepare_count = 0
    cleanup_count = 0

    class Lease:
        ownership_lost = False

        async def start_heartbeat(self):
            return None

        async def ensure_publication_window(self, _seconds):
            return True

        async def release(self):
            return None

    async def tool_config(*_args):
        return {
            "workspace_mode": "isolated_output",
            "publication_owner": publication_owner,
        }

    async def resolve_scope(**_kwargs):
        return SandboxExecutionScope(tenant_id, agent_id, session_id)

    async def acquire(*_args, **_kwargs):
        return Lease()

    async def prepare(*_args, **_kwargs):
        nonlocal prepare_count, cleanup_count
        prepare_count += 1

        def cleanup():
            nonlocal cleanup_count
            cleanup_count += 1

        return SimpleNamespace(root=tmp_path, cleanup=cleanup)

    async def flush(_workspace, conflict_mode):
        conflict_modes.append(conflict_mode)
        return {"updated": [], "deleted": [], "conflicted": [], "skipped": []}

    async def execute(*_args, gateway_publish=None, **_kwargs):
        if gateway_publish is not None and publication_owner == "gateway":
            await gateway_publish()
        return ToolExecutionOutcome("succeeded", "ok", None)

    monkeypatch.setattr(agent_tools, "_get_tool_config", tool_config)
    monkeypatch.setattr(agent_tools, "_resolve_sandbox_execution_scope", resolve_scope)
    monkeypatch.setattr(SandboxExecutionLeaseStore, "acquire", acquire)
    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", prepare)
    monkeypatch.setattr(agent_tools, "flush_temp_workspace", flush)
    monkeypatch.setattr(agent_tools, "_execute_code_outcome", execute)
    monkeypatch.setattr("app.config.get_sandbox_config", lambda: SandboxConfig())

    run_id = str(uuid.uuid4())
    token = sandbox_run_scope_id.set(run_id)
    try:
        first = await agent_tools._execute_code_with_workspace_outcome(
            agent_id=agent_id,
            tenant_id=str(tenant_id),
            session_id=str(session_id),
            arguments={"language": "python", "code": "print(1)"},
            tool_name="execute_code",
        )
        second = await agent_tools._execute_code_with_workspace_outcome(
            agent_id=agent_id,
            tenant_id=str(tenant_id),
            session_id=str(session_id),
            arguments={"language": "python", "code": "print(2)"},
            tool_name="execute_code",
        )
    finally:
        sandbox_run_scope_id.reset(token)
        await close_run_workspace(run_id)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert conflict_modes == ["overwrite", "overwrite"]
    assert prepare_count == 1
    assert cleanup_count == 1
