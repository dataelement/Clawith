from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.agent_seeder import OKR_AGENT_SOUL
from app.services.builtin_tool_definitions import builtin_model_definition


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _mcp_binding_session(tool, assignment):
    @asynccontextmanager
    async def factory():
        class Session:
            def __init__(self) -> None:
                self.results = iter((tool, assignment))

            async def execute(self, statement):
                del statement
                return _ScalarResult(next(self.results))

        yield Session()

    return factory


def _definition(name: str) -> dict:
    definition = builtin_model_definition(name)
    assert definition is not None
    return definition["function"]


def test_feishu_drive_share_schema_does_not_offer_unsupported_name_lookup() -> None:
    schema = _definition("feishu_drive_share")["parameters"]

    assert "member_open_ids" in schema["properties"]
    assert "member_names" not in schema["properties"]


@pytest.mark.asyncio
async def test_typed_doc_create_rejects_legacy_wiki_arguments_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = 0

    async def credentials(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        return None, None, ToolExecutionOutcome(
            status="failed",
            result_summary="provider path must not run",
            result_ref=None,
            error_code="unexpected_provider_access",
        )

    monkeypatch.setattr(agent_tools, "_feishu_credentials_outcome", credentials)
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "feishu_doc_create",
        {
            "title": "Legacy Wiki document",
            "wiki_space_id": "space-legacy",
            "parent_node_token": "node-legacy",
        },
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_tool_arguments_unsupported"
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_typed_calendar_create_rejects_legacy_direct_attendees_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = 0

    async def calendar_context(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        return None, None, ToolExecutionOutcome(
            status="failed",
            result_summary="provider path must not run",
            result_ref=None,
            error_code="unexpected_provider_access",
        )

    monkeypatch.setattr(
        agent_tools,
        "_feishu_calendar_context_outcome",
        calendar_context,
    )
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "feishu_calendar_create",
        {
            "summary": "Legacy attendee event",
            "start_time": "2026-07-16T09:00:00+08:00",
            "end_time": "2026-07-16T10:00:00+08:00",
            "attendee_open_ids": ["ou_legacy"],
            "attendee_emails": ["legacy@example.com"],
        },
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_tool_arguments_unsupported"
    assert provider_calls == 0


def test_okr_report_contracts_describe_bounded_receipts_not_full_markdown() -> None:
    for name in ("generate_okr_report", "generate_monthly_okr_report"):
        description = _definition(name)["description"].lower()
        assert "full" not in description
        assert "plaza" not in description
        assert "receipt" in description or "reference" in description


def test_okr_agent_prompt_uses_report_receipt_without_disabled_plaza_tool() -> None:
    normalized = OKR_AGENT_SOUL.lower()

    assert "plaza_create_post" not in normalized
    assert "generate_okr_report" in normalized
    assert "receipt" in normalized or "reference" in normalized


@pytest.mark.asyncio
async def test_legacy_image_generation_only_serializes_the_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    async def typed_outcome(agent_id, workspace, arguments, provider):
        nonlocal calls
        calls += 1
        assert workspace == tmp_path
        assert arguments == {"prompt": "a quiet mountain"}
        assert provider == "openai"
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Image generated with a durable workspace receipt.",
            result_ref=f"workspace://{agent_id}/workspace/images/result.png",
        )

    monkeypatch.setattr(agent_tools, "_generate_image_outcome", typed_outcome)
    result = await agent_tools._generate_image(
        uuid.uuid4(),
        tmp_path,
        {"prompt": "a quiet mountain"},
        "openai",
    )

    assert calls == 1
    assert result == "✅ Image generated with a durable workspace receipt."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "adapter_name"),
    (
        ("read_file", {"path": "workspace/report.md"}, "_read_file_outcome"),
        (
            "agentbay_code_read_file",
            {"remote_path": "/tmp/report.md"},
            "_agentbay_read_outcome",
        ),
    ),
)
async def test_registered_builtin_and_agentbay_read_keep_typed_adapters(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict,
    adapter_name: str,
) -> None:
    calls: list[tuple[tuple, dict]] = []

    async def adapter(*args, **kwargs):
        calls.append((args, kwargs))
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary=f"{tool_name} typed receipt",
            result_ref=None,
        )

    monkeypatch.setattr(agent_tools, adapter_name, adapter)
    if tool_name == "read_file":
        async def tenant(_agent_id):
            return str(uuid.uuid4())

        monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant)

    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        uuid.uuid4(),
        uuid.uuid4(),
        session_id="session-registered",
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert calls


@pytest.mark.asyncio
async def test_registered_dynamic_mcp_keeps_exact_typed_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid.uuid4()
    target = {
        "full_name": "tenant_search",
        "raw_name": "search",
        "server_url": "https://mcp.example.test",
    }
    calls: list[tuple[dict, dict, uuid.UUID]] = []

    async def resolve(tool_name, resolved_agent_id):
        assert tool_name == "tenant_search"
        assert resolved_agent_id == agent_id
        return target

    async def execute(resolved_target, arguments, *, agent_id):
        calls.append((resolved_target, arguments, agent_id))
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="MCP typed receipt",
            result_ref=None,
        )

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr(
        agent_tools,
        "_execute_resolved_mcp_target_outcome",
        execute,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        "tenant_search",
        {"query": "contract"},
        agent_id,
        uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert calls == [(target, {"query": "contract"}, agent_id)]


@pytest.mark.asyncio
async def test_registered_dynamic_mcp_uses_frozen_binding_without_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid.uuid4()
    binding = {
        "kind": "mcp",
        "handler_key": "tenant_search",
        "target": {
            "tool_id": str(uuid.uuid4()),
            "route_digest": "digest",
        },
        "credential_ref": str(uuid.uuid4()),
    }
    target = {
        "full_name": "tenant_search",
        "raw_name": "search",
        "server_url": "https://frozen.example/mcp",
        "config": {},
    }
    calls: list[tuple[dict, dict, uuid.UUID]] = []

    async def live_name_lookup_forbidden(*args, **kwargs):
        raise AssertionError(f"frozen binding used live name lookup: {args}, {kwargs}")

    async def resolve_frozen(raw_binding, resolved_agent_id):
        assert raw_binding == binding
        assert resolved_agent_id == agent_id
        return target

    async def execute(resolved_target, arguments, *, agent_id):
        calls.append((resolved_target, arguments, agent_id))
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="MCP typed receipt",
            result_ref=None,
        )

    monkeypatch.setattr(
        agent_tools,
        "_resolve_mcp_execution_target",
        live_name_lookup_forbidden,
    )
    monkeypatch.setattr(
        agent_tools,
        "_resolve_frozen_mcp_execution_target",
        resolve_frozen,
        raising=False,
    )
    monkeypatch.setattr(
        agent_tools,
        "_execute_resolved_mcp_target_outcome",
        execute,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        "tenant_search",
        {"query": "contract"},
        agent_id,
        uuid.uuid4(),
        execution_binding=binding,
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert calls == [(target, {"query": "contract"}, agent_id)]


@pytest.mark.asyncio
async def test_frozen_mcp_binding_resolves_assignment_and_rejects_route_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    tool = SimpleNamespace(
        id=tool_id,
        name="tenant_search",
        enabled=True,
        mcp_server_url="https://frozen.example/mcp",
        mcp_server_name="search",
        mcp_tool_name="lookup",
        config={},
        config_schema={},
    )
    assignment = SimpleNamespace(
        id=assignment_id,
        agent_id=agent_id,
        tool_id=tool_id,
        enabled=True,
        config={},
    )
    monkeypatch.setattr(
        agent_tools,
        "async_session",
        _mcp_binding_session(tool, assignment),
    )

    binding = {
        "kind": "mcp",
        "handler_key": "tenant_search",
        "target": {
            "tool_id": str(tool_id),
            "route_digest": agent_tools._mcp_route_digest(
                server_url="https://frozen.example/mcp",
                server_name="search",
                raw_name="lookup",
                async_completion=None,
            ),
        },
        "credential_ref": str(assignment_id),
    }

    target = await agent_tools._resolve_frozen_mcp_execution_target(
        binding,
        agent_id,
    )

    assert target == {
        "full_name": "tenant_search",
        "raw_name": "lookup",
        "server_url": "https://frozen.example/mcp",
        "server_name": "search",
        "config": {},
        "async_completion": None,
    }

    tool.mcp_server_url = "https://changed.example/mcp"
    target = await agent_tools._resolve_frozen_mcp_execution_target(
        binding,
        agent_id,
    )

    assert target == {
        "full_name": "tenant_search",
        "unavailable_error_code": "mcp_binding_changed",
    }
