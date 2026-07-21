"""Canonical builtin tool contracts and Runtime outcome adapters."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import inspect
from types import SimpleNamespace
import uuid

import pytest

from app.services import agent_tools, tool_seeder
from app.services.builtin_tool_definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    BUILTIN_TOOL_NAMES,
    BUILTIN_TOOL_SEEDS,
    GROUP_RUNTIME_TOOL_DEFINITIONS,
    builtin_cross_space_action,
    builtin_model_definition,
    builtin_policy,
    is_reserved_custom_tool_name,
    validate_builtin_tool_definitions,
)
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome


def _model_by_name() -> dict[str, dict]:
    return {
        tool["function"]["name"]: tool
        for tool in agent_tools.AGENT_TOOLS
    }


def test_builtin_contract_has_unique_valid_names_and_complete_runtime_policy() -> None:
    validate_builtin_tool_definitions()

    names = [definition["name"] for definition in BUILTIN_TOOL_DEFINITIONS]
    assert len(names) == len(set(names))
    assert frozenset(names) == BUILTIN_TOOL_NAMES
    assert {"get_okr", "get_my_okr", "update_kr_progress", "update_kr_content"} <= BUILTIN_TOOL_NAMES
    for definition in BUILTIN_TOOL_DEFINITIONS:
        assert definition["description"].strip()
        schema = definition["parameters_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)
        assert definition["effect"] in {"read", "write", "external_write"}
        assert definition["retry_policy"] in {"safe", "conditional", "never"}
        assert definition["readiness"]
        assert isinstance(definition["sensitive_paths"], tuple)


def test_seeder_and_model_contracts_are_derived_from_the_same_builtin_source() -> None:
    seed_by_name = {seed["name"]: seed for seed in BUILTIN_TOOL_SEEDS}
    compatibility_seed_by_name = {
        seed["name"]: seed for seed in tool_seeder.BUILTIN_TOOLS
    }
    model_by_name = _model_by_name()

    assert seed_by_name == compatibility_seed_by_name
    assert set(model_by_name) == (
        BUILTIN_TOOL_NAMES - agent_tools._HIDDEN_FROM_LLM_TOOL_NAMES
    )
    for name, seed in seed_by_name.items():
        if name in agent_tools._HIDDEN_FROM_LLM_TOOL_NAMES:
            assert name not in model_by_name
            continue
        model = model_by_name[name]["function"]
        assert model == builtin_model_definition(name)["function"]
        assert model["description"] == seed["description"]
        assert model["parameters"] == seed["parameters_schema"]


def test_builtin_model_definition_ignores_stale_database_contract() -> None:
    stale = {
        "type": "function",
        "function": {
            "name": "send_channel_message",
            "description": "stale database description",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    canonical = agent_tools._canonicalize_llm_tool(deepcopy(stale), source="builtin")

    assert canonical == builtin_model_definition("send_channel_message")
    assert canonical != stale


def test_known_schema_contracts_match_handler_validation() -> None:
    send_channel = builtin_model_definition("send_channel_message")["function"]["parameters"]
    send_platform = builtin_model_definition("send_platform_message")["function"]["parameters"]
    upload_image = builtin_model_definition("upload_image")["function"]["parameters"]
    update_trigger = builtin_model_definition("update_trigger")["function"]["parameters"]
    set_trigger = builtin_model_definition("set_trigger")["function"]["parameters"]
    import_mcp = builtin_model_definition("import_mcp_server")["function"]["parameters"]

    assert send_channel["required"] == ["target_member_id", "message"]
    assert send_platform["required"] == ["message"]
    assert send_platform["anyOf"] == [
        {"required": ["target_member_id"]},
        {"required": ["platform_user_id"]},
    ]
    assert upload_image["oneOf"] == [
        {"required": ["file_path"]},
        {"required": ["url"]},
    ]
    assert "anyOf" not in upload_image
    assert update_trigger["anyOf"] == [
        {"required": ["config"]},
        {"required": ["reason"]},
    ]
    assert "webhook" in set_trigger["properties"]["type"]["enum"]
    assert "reauthorize" in import_mcp["properties"]


@pytest.mark.parametrize(
    "name",
    ["finish", "wait", "group_query_members", "group_future_tool"],
)
def test_runtime_reserved_tool_names_cannot_be_overridden(name: str) -> None:
    assert is_reserved_custom_tool_name(name)


def test_group_runtime_tools_are_served_from_the_canonical_data_module() -> None:
    from app.services.agent_runtime import group_runtime_tools

    assert group_runtime_tools.GROUP_RUNTIME_TOOL_DEFINITIONS is GROUP_RUNTIME_TOOL_DEFINITIONS
    names = {
        tool["function"]["name"] for tool in GROUP_RUNTIME_TOOL_DEFINITIONS
    }
    assert names == group_runtime_tools.GROUP_TOOL_NAMES
    assert "agent_id" in builtin_model_definition("group_query_members")["function"]["description"]


def test_non_reserved_dynamic_tool_keeps_conservative_policy() -> None:
    assert not is_reserved_custom_tool_name("tenant_search")
    assert builtin_policy("tenant_search") == {
        "effect": "external_write",
        "retry_policy": "never",
        "parallel_safe": False,
    }


def test_cross_space_aliases_share_two_canonical_actions() -> None:
    assert {
        name: builtin_cross_space_action(name)
        for name in (
            "send_channel_message",
            "send_platform_message",
            "send_feishu_message",
        )
    } == {
        "send_channel_message": "external_message",
        "send_platform_message": "external_message",
        "send_feishu_message": "external_message",
    }
    assert {
        name: builtin_cross_space_action(name)
        for name in ("send_channel_file", "send_file_to_agent")
    } == {
        "send_channel_file": "external_file",
        "send_file_to_agent": "external_file",
    }
    assert builtin_cross_space_action("send_message_to_agent") is None


def test_canonical_sensitive_paths_are_consumed_by_observability_sanitizer() -> None:
    sanitized = agent_tools._observability_arguments(
        "vercel_set_env",
        {"key": "PUBLIC_NAME", "value": "not-secret-shaped-but-sensitive"},
    )

    assert sanitized == {"key": "PUBLIC_NAME", "value": "[REDACTED]"}
    assert builtin_policy("read_document") == {
        "effect": "read",
        "retry_policy": "safe",
        "parallel_safe": True,
    }


def test_durable_runtime_default_executor_preserves_typed_outcomes() -> None:
    from app.services.agent_runtime.model_step_service import RuntimeModelStepService
    from app.services.agent_runtime.tool_step_service import RuntimeToolStepService

    executor_default = inspect.signature(RuntimeToolStepService.__init__).parameters[
        "tool_executor"
    ].default
    tool_provider_default = inspect.signature(RuntimeToolStepService.__init__).parameters[
        "tool_provider"
    ].default
    model_provider_default = inspect.signature(RuntimeModelStepService.__init__).parameters[
        "tool_provider"
    ].default
    assert executor_default is agent_tools.execute_builtin_tool_outcome
    assert tool_provider_default is agent_tools.get_runtime_agent_tools_for_llm
    assert model_provider_default is agent_tools.get_runtime_agent_tools_for_llm


def test_runtime_resolver_hides_every_application_tool_without_typed_boundary() -> None:
    tools = [
        builtin_model_definition("read_file"),
        builtin_model_definition("read_webpage"),
        {
            "type": "function",
            "function": {
                "name": "tenant_dynamic_tool",
                "description": "dynamic",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    resolved = agent_tools._runtime_typed_tools(tools)

    assert [tool["function"]["name"] for tool in resolved] == [
        "read_file",
        "read_webpage",
    ]
    assert agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES <= BUILTIN_TOOL_NAMES


def test_local_content_batch_has_native_runtime_outcomes_before_becoming_visible() -> None:
    expected = {
        "execute_code",
        "convert_csv_to_xlsx",
        "convert_html_to_pdf",
        "convert_html_to_pptx",
        "convert_markdown_to_docx",
        "convert_markdown_to_pdf",
        "read_document",
        "read_webpage",
        "upload_image",
        "publish_page",
        "list_published_pages",
    }

    assert expected <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_focus_read_and_write_handlers_return_native_typed_outcomes(
    monkeypatch,
) -> None:
    async def fake_list(*args, **kwargs):
        return [
            {
                "key": "ship",
                "title": "Ship",
                "description": "Ship the release",
                "status": "in_progress",
                "kind": "normal",
            }
        ]

    async def fake_upsert(*args, **kwargs):
        return {
            "key": "ship",
            "title": "Ship",
            "description": "Ship the release",
        }

    monkeypatch.setattr(agent_tools, "list_focus_items", fake_list)
    monkeypatch.setattr(agent_tools, "upsert_focus_item", fake_upsert)

    read_outcome = await agent_tools.execute_builtin_tool_outcome(
        "list_focus_items", {}, agent_id=None, user_id=None
    )
    write_outcome = await agent_tools.execute_builtin_tool_outcome(
        "upsert_focus_item",
        {"description": "Ship the release", "title": "Ship"},
        agent_id=None,
        user_id=None,
    )

    assert isinstance(read_outcome, ToolExecutionOutcome)
    assert read_outcome.status == "succeeded"
    assert "Ship the release" in (read_outcome.result_summary or "")
    assert isinstance(write_outcome, ToolExecutionOutcome)
    assert write_outcome.status == "succeeded"
    assert "ship" in (write_outcome.result_summary or "")


@pytest.mark.asyncio
async def test_typed_builtin_validation_failure_is_explicit_not_unknown() -> None:
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "upsert_focus_item", {}, agent_id=None, user_id=None
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "invalid_tool_arguments"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("move_file", {}),
        ("delete_file", {}),
        ("edit_file", {}),
        ("search_files", {}),
        ("find_files", {}),
        ("update_trigger", {}),
        ("cancel_trigger", {}),
        ("query_directory", {"limit": "not-an-integer"}),
    ],
)
async def test_runtime_visible_local_tools_return_typed_validation_failures(
    tool_name: str,
    arguments: dict,
) -> None:
    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code


@pytest.mark.asyncio
async def test_external_provider_typed_outcome_is_preserved_without_string_guessing(
    monkeypatch,
) -> None:
    provider_outcome = ToolExecutionOutcome(
        status="unknown",
        result_summary="Provider request may have been accepted.",
        result_ref=None,
        error_code="provider_response_unknown",
    )

    async def fake_channel_outcome(*args, **kwargs):
        return provider_outcome

    monkeypatch.setattr(
        agent_tools,
        "_send_channel_message_outcome",
        fake_channel_outcome,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        "send_channel_message",
        {"target_member_id": "member-id", "message": "hello"},
        agent_id=None,
        user_id=None,
    )

    assert outcome is provider_outcome


@pytest.mark.asyncio
async def test_feishu_provider_timeout_is_native_unknown_and_rejection_is_failed(
    monkeypatch,
) -> None:
    from app.services.feishu_service import FeishuAPIError, feishu_service

    class _ScalarResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(app_id="app", app_secret="secret")

    class _DB:
        async def execute(self, statement):
            del statement
            return _ScalarResult()

    @asynccontextmanager
    async def fake_session():
        yield _DB()

    target = SimpleNamespace(external_id="ou_target")
    monkeypatch.setattr(agent_tools, "async_session", fake_session)

    async def timeout(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("network timeout")

    monkeypatch.setattr(feishu_service, "send_message", timeout)
    unknown = await agent_tools._send_feishu_message_to_member_outcome(
        uuid.uuid4(), "Target", "hello", target
    )
    assert unknown.status == "unknown"
    assert unknown.error_code == "feishu_message_outcome_unknown"

    async def rejected(*args, **kwargs):
        del args, kwargs
        raise FeishuAPIError(stage="send_message", code=230001, msg="denied")

    monkeypatch.setattr(feishu_service, "send_message", rejected)
    failed = await agent_tools._send_feishu_message_to_member_outcome(
        uuid.uuid4(), "Target", "hello", target
    )
    assert failed.status == "failed"
    assert failed.error_code == "feishu_message_rejected"


@pytest.mark.asyncio
async def test_unknown_dynamic_tool_string_is_not_promoted_to_typed_success(
    monkeypatch,
) -> None:
    async def fake_legacy(*args, **kwargs):
        return "looks fine"

    monkeypatch.setattr(agent_tools, "execute_tool", fake_legacy)
    result = await agent_tools.execute_builtin_tool_outcome(
        "tenant_dynamic_tool", {}, agent_id=None, user_id=None
    )

    assert result == "looks fine"
    assert not isinstance(result, ToolExecutionOutcome)


@pytest.mark.asyncio
async def test_legacy_execute_tool_consumer_still_receives_model_text(
    monkeypatch,
) -> None:
    async def fake_list(*args, **kwargs):
        return []

    async def fake_tenant(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_tools, "list_focus_items", fake_list)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", fake_tenant)

    result = await agent_tools.execute_tool(
        "list_focus_items",
        {},
        agent_id=None,
        user_id=None,
    )

    assert result == "No Focus items."
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_legacy_caller_blocks_tools_outside_the_resolved_workset(
    monkeypatch,
) -> None:
    from app.services.llm import caller

    executed = False

    async def forbidden_execute(*args, **kwargs):
        nonlocal executed
        executed = True
        return "must not run"

    monkeypatch.setattr(caller, "execute_tool", forbidden_execute)
    messages = []
    await caller._process_tool_call(
        {
            "id": "call-disabled",
            "function": {"name": "write_file", "arguments": "{}"},
        },
        messages,
        agent_id=None,
        user_id=None,
        session_id="",
        supports_vision=False,
        on_tool_call=None,
        full_reasoning_content="",
        allowed_tool_names={"read_file"},
    )

    assert executed is False
    assert len(messages) == 1
    assert "not enabled" in str(messages[0].content)


@pytest.mark.asyncio
async def test_workspace_initialization_does_not_copy_role_metadata_into_soul(
    monkeypatch,
) -> None:
    class _Storage:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def is_file(self, key: str) -> bool:
            return key in self.values

        async def write_text(self, key: str, value: str, **kwargs) -> None:
            del kwargs
            self.values[key] = value

    storage = _Storage()
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)
    agent_id = uuid.uuid4()

    await agent_tools.initialize_agent_workspace(agent_id)

    soul = storage.values[f"{agent_id}/soul.md"]
    assert "role" not in soul.lower()
    assert "responsibilit" not in soul.lower()
    assert "personality, values, and working style" in soul
