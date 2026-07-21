"""D-020 adapters for the remaining default, assignable builtin tools."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools, resource_discovery
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    builtin_model_definition,
)


REMAINING_DEFAULT_TYPED_TOOLS = {
    "set_trigger",
    "send_channel_file",
    "send_file_to_agent",
    "duckduckgo_search",
    "search_experience",
    "read_experience",
    "propose_experience_draft",
    "discover_resources",
    "import_mcp_server",
    "update_objective",
    "search_clawhub",
    "install_skill",
}


def test_remaining_default_tools_are_runtime_visible_only_after_typed_migration() -> None:
    assert REMAINING_DEFAULT_TYPED_TOOLS <= (
        agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    )
    assert "send_channel_message" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    default_application_tools = {
        definition["name"]
        for definition in BUILTIN_TOOL_DEFINITIONS
        if definition["is_default"] and definition["name"] != "finish"
    }
    assert default_application_tools <= (
        agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    )


@pytest.mark.asyncio
async def test_runtime_resolver_applies_channel_and_registry_readiness(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tools = [
        builtin_model_definition("send_channel_file"),
        builtin_model_definition("discover_resources"),
        builtin_model_definition("import_mcp_server"),
        builtin_model_definition("duckduckgo_search"),
    ]

    async def fake_tools(_agent_id):
        return tools

    async def no_channel(_agent_id):
        return False

    async def no_credentials(_agent_id, _name):
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", fake_tools)
    monkeypatch.setattr(agent_tools, "_agent_has_any_channel", no_channel)
    monkeypatch.setattr(agent_tools, "_get_tool_config", no_credentials)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(agent_id)
    assert [tool["function"]["name"] for tool in resolved] == [
        "duckduckgo_search"
    ]

    async def has_channel(_agent_id):
        return True

    async def configured(_agent_id, name):
        if name == "discover_resources":
            return {"modelscope_api_token": "configured"}
        if name == "import_mcp_server":
            return {"smithery_api_key": "configured"}
        return {}

    monkeypatch.setattr(agent_tools, "_agent_has_any_channel", has_channel)
    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(agent_id)
    assert [tool["function"]["name"] for tool in resolved] == [
        "send_channel_file",
        "discover_resources",
        "import_mcp_server",
        "duckduckgo_search",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(REMAINING_DEFAULT_TYPED_TOOLS))
async def test_remaining_default_tools_have_native_typed_validation_failures(
    tool_name: str,
) -> None:
    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        {},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_duckduckgo_uses_http_and_parse_facts_and_timeout_is_retryable(
    monkeypatch,
) -> None:
    class Response:
        status_code = 200
        text = (
            '<a class="result__a" href="https://example.test">Example</a>'
            '<a class="result__snippet">Verified snippet</a>'
        )

    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *args, **kwargs):
            del args, kwargs
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    success = await agent_tools._duckduckgo_search_outcome(
        {"query": "verified", "max_results": 3}
    )
    assert success.status == "succeeded"
    assert "Verified snippet" in (success.result_summary or "")

    class TimeoutClient(Client):
        async def get(self, *args, **kwargs):
            del args, kwargs
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    timeout = await agent_tools._duckduckgo_search_outcome(
        {"query": "verified"}
    )
    assert timeout.status == "failed"
    assert timeout.error_code == "duckduckgo_timeout"
    assert timeout.retryable is True


@pytest.mark.asyncio
async def test_channel_file_marks_post_dispatch_exception_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "report.txt").write_text("report", encoding="utf-8")

    async def timeout_sender(*args, **kwargs):
        del args, kwargs
        raise httpx.TimeoutException("timeout after dispatch")

    token = agent_tools.channel_file_sender.set(timeout_sender)
    try:
        outcome = await agent_tools._send_channel_file_outcome(
            uuid.uuid4(),
            tmp_path,
            {"file_path": "report.txt"},
        )
    finally:
        agent_tools.channel_file_sender.reset(token)

    assert outcome.status == "unknown"
    assert outcome.error_code == "channel_file_outcome_unknown"


@pytest.mark.asyncio
async def test_channel_file_web_fallback_emits_only_a_current_workspace_ref(
    tmp_path: Path,
) -> None:
    agent_id = uuid.uuid4()
    (tmp_path / "report.txt").write_text("report", encoding="utf-8")

    outcome = await agent_tools._send_channel_file_outcome(
        agent_id,
        tmp_path,
        {"file_path": "report.txt"},
    )

    assert outcome.status == "succeeded"
    assert outcome.artifact_refs == (f"workspace://{agent_id}/report.txt",)
    assert outcome.evidence_refs == ()


@pytest.mark.asyncio
async def test_propose_experience_draft_is_a_validated_no_write_success() -> None:
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "propose_experience_draft",
        {
            "title": "A bounded draft",
            "body": "## Scene\nEvidence",
            "applicability": "Use only while the contract remains true.",
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert outcome.artifact_refs == ()
    assert outcome.evidence_refs == ()


@pytest.mark.asyncio
async def test_set_trigger_commit_uncertainty_is_unknown(monkeypatch) -> None:
    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar(self):
            return self.value

    class DB:
        def __init__(self):
            self.results = [
                ScalarResult(SimpleNamespace(max_triggers=20)),
                ScalarResult(0),
                ScalarResult(None),
            ]

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, _value):
            return None

        async def commit(self):
            raise RuntimeError("commit response lost")

    @asynccontextmanager
    async def fake_session():
        yield DB()

    async def focus(*args, **kwargs):
        del args, kwargs
        return "focus-key"

    monkeypatch.setattr(agent_tools, "async_session", fake_session)
    monkeypatch.setattr(agent_tools, "ensure_focus_item", focus)

    outcome = await agent_tools._handle_set_trigger_outcome(
        uuid.uuid4(),
        {
            "name": "daily",
            "type": "interval",
            "config": {"minutes": 30},
            "reason": "Check progress",
        },
    )

    assert outcome.status == "unknown"
    assert outcome.error_code == "trigger_create_outcome_unknown"


@pytest.mark.asyncio
async def test_registry_discovery_uses_structured_provider_results(monkeypatch) -> None:
    async def smithery_key(_agent_id):
        return "configured"

    async def no_modelscope(_agent_id):
        return ""

    async def smithery_results(*args, **kwargs):
        del args, kwargs
        return [
            {
                "name": "acme/search",
                "display_name": "Acme Search",
                "description": "Search service",
                "remote": True,
                "verified": True,
                "use_count": 3,
                "homepage": "https://example.test/acme-search",
                "source": "Smithery",
            }
        ]

    monkeypatch.setattr(
        resource_discovery, "_get_smithery_api_key", smithery_key
    )
    monkeypatch.setattr(
        resource_discovery, "_get_modelscope_api_token", no_modelscope
    )
    monkeypatch.setattr(
        resource_discovery, "_search_smithery_api", smithery_results
    )

    outcome = await resource_discovery.search_registries_outcome(
        "search",
        agent_id=uuid.uuid4(),
    )

    assert outcome.status == "succeeded"
    assert "acme/search" in (outcome.result_summary or "")
    assert outcome.artifact_refs == ()


@pytest.mark.asyncio
async def test_registry_timeout_is_failed_and_retryable(monkeypatch) -> None:
    async def smithery_key(_agent_id):
        return "configured"

    async def no_modelscope(_agent_id):
        return ""

    async def timeout(*args, **kwargs):
        del args, kwargs
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(
        resource_discovery, "_get_smithery_api_key", smithery_key
    )
    monkeypatch.setattr(
        resource_discovery, "_get_modelscope_api_token", no_modelscope
    )
    monkeypatch.setattr(resource_discovery, "_search_smithery_api", timeout)

    outcome = await resource_discovery.search_registries_outcome(
        "search",
        agent_id=uuid.uuid4(),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "resource_discovery_failed"
    assert outcome.retryable is True


@pytest.mark.asyncio
async def test_import_mcp_preserves_native_success_and_unknown(monkeypatch) -> None:
    success = ToolExecutionOutcome(
        status="succeeded",
        result_summary="Imported one MCP server.",
        result_ref=None,
    )

    async def imported(*args, **kwargs):
        del args, kwargs
        return success

    monkeypatch.setattr(
        resource_discovery,
        "import_mcp_from_smithery_outcome",
        imported,
    )
    result = await agent_tools._import_mcp_server_outcome(
        uuid.uuid4(),
        {"server_id": "acme/search"},
    )
    assert result is success

    async def transport_lost(*args, **kwargs):
        del args, kwargs
        raise httpx.ReadError("response lost")

    monkeypatch.setattr(
        resource_discovery,
        "import_mcp_from_smithery_outcome",
        transport_lost,
    )
    unknown = await agent_tools._import_mcp_server_outcome(
        uuid.uuid4(),
        {"server_id": "acme/search"},
    )
    assert unknown.status == "unknown"
    assert unknown.error_code == "mcp_import_outcome_unknown"


@pytest.mark.asyncio
async def test_clawhub_search_and_skill_install_use_decoded_payloads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.api import skills as skills_api

    async def tenant(_agent_id):
        return "tenant"

    async def no_key(_tenant_id):
        return ""

    async def search(*args, **kwargs):
        del args, kwargs
        return {
            "results": [
                {
                    "displayName": "Research",
                    "slug": "research",
                    "summary": "Grounded research workflow",
                }
            ]
        }, "https://clawhub.test/api"

    async def meta(*args, **kwargs):
        del args, kwargs
        return {}, "https://clawhub.test/api"

    async def archive(*args, **kwargs):
        del args, kwargs
        return [
            {"path": "SKILL.md", "content": "# Research"},
            {"path": "references/checklist.md", "content": "Verify"},
        ], "https://clawhub.test/api"

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant)
    monkeypatch.setattr(skills_api, "_get_clawhub_key", no_key)
    monkeypatch.setattr(skills_api, "_fetch_clawhub_json", search)
    monkeypatch.setattr(skills_api, "_fetch_clawhub_skill_meta", meta)
    monkeypatch.setattr(skills_api, "_fetch_clawhub_skill_archive", archive)

    search_outcome = await agent_tools._search_clawhub_outcome(
        uuid.uuid4(),
        {"query": "research"},
    )
    assert search_outcome.status == "succeeded"
    assert "Grounded research workflow" in (
        search_outcome.result_summary or ""
    )

    agent_id = uuid.uuid4()
    install_outcome = await agent_tools._install_skill_outcome(
        agent_id,
        tmp_path,
        {"source": "research"},
    )
    assert install_outcome.status == "succeeded"
    assert (tmp_path / "skills/research/SKILL.md").is_file()
    assert install_outcome.artifact_refs == (
        f"workspace://{agent_id}/skills/research/SKILL.md",
        f"workspace://{agent_id}/skills/research/references/checklist.md",
    )
