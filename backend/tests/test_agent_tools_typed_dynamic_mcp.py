"""D-020 Durable Runtime boundary for dynamically assigned MCP tools."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.mcp_client import MCPClient


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _async_completion_contract() -> dict:
    return {
        "version": 1,
        "result": {
            "source": "content_text_json",
            "content_index": 0,
            "status_pointer": "/status",
        },
        "operation_id": {"source": "argument", "pointer": "/paper_id"},
        "states": {
            "pending": ["downloading", "converting", "running"],
            "succeeded": ["success"],
            "failed": ["error"],
            "unknown": ["unknown"],
        },
        "poll": {
            "tool": "$self",
            "copy_arguments": ["/paper_id"],
            "set_arguments": {"/check_status": True},
            "interval_ms": 1000,
        },
    }


@pytest.mark.asyncio
async def test_runtime_resolver_exposes_only_enabled_assigned_non_reserved_mcp(
    monkeypatch,
) -> None:
    tools = [
        _tool("mcp_visible_lookup"),
        _tool("mcp_disabled_lookup"),
        _tool("finish"),
        _tool("wait"),
        _tool("group_private_lookup"),
        _tool("generate_image_openai"),
    ]

    async def assigned(_agent_id):
        return tools

    async def dynamic_names(_agent_id):
        # The DB resolver returns only locally ready rows whose Tool and
        # AgentTool records are both enabled.
        return {
            "mcp_visible_lookup",
            "finish",
            "wait",
            "group_private_lookup",
            "generate_image_openai",
        }

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        dynamic_names,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert [item["function"]["name"] for item in resolved] == [
        "mcp_visible_lookup"
    ]


@pytest.mark.asyncio
async def test_runtime_mcp_readiness_is_local_and_never_pings_provider(
    monkeypatch,
) -> None:
    async def assigned(_agent_id):
        return [_tool("mcp_visible_lookup")]

    async def dynamic_names(_agent_id):
        return {"mcp_visible_lookup"}

    async def network_forbidden(*_args, **_kwargs):
        raise AssertionError("model-step readiness must not ping MCP providers")

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        dynamic_names,
    )
    monkeypatch.setattr(MCPClient, "list_tools", network_forbidden)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    assert [item["function"]["name"] for item in resolved] == [
        "mcp_visible_lookup"
    ]


@pytest.mark.asyncio
async def test_durable_mcp_uses_exact_full_name_when_raw_names_collide(
    monkeypatch,
) -> None:
    targets = {
        "mcp_alpha_lookup": {
            "full_name": "mcp_alpha_lookup",
            "raw_name": "lookup",
            "server_url": "https://alpha.example/mcp",
            "server_name": "alpha",
            "config": {},
        },
        "mcp_beta_lookup": {
            "full_name": "mcp_beta_lookup",
            "raw_name": "lookup",
            "server_url": "https://beta.example/mcp",
            "server_name": "beta",
            "config": {},
        },
    }
    resolved_names: list[tuple[str, bool]] = []
    calls: list[tuple[str, str]] = []

    async def resolve(tool_name, _agent_id, *, allow_legacy_bare_name=False):
        resolved_names.append((tool_name, allow_legacy_bare_name))
        return targets.get(tool_name)

    async def raw_call(self, raw_name, _arguments):
        calls.append((self.server_url, raw_name))
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr(MCPClient, "call_tool_result", raw_call)

    alpha = await agent_tools._execute_mcp_tool_outcome(
        "mcp_alpha_lookup", {}, agent_id=uuid.uuid4()
    )
    beta = await agent_tools._execute_mcp_tool_outcome(
        "mcp_beta_lookup", {}, agent_id=uuid.uuid4()
    )

    assert alpha.status == beta.status == "succeeded"
    assert resolved_names == [
        ("mcp_alpha_lookup", False),
        ("mcp_beta_lookup", False),
    ]
    assert calls == [
        ("https://alpha.example/mcp", "lookup"),
        ("https://beta.example/mcp", "lookup"),
    ]


@pytest.mark.asyncio
async def test_durable_mcp_never_resolves_a_bare_raw_name(monkeypatch) -> None:
    async def resolve(tool_name, _agent_id, *, allow_legacy_bare_name=False):
        assert tool_name == "lookup"
        assert allow_legacy_bare_name is False
        return None

    class ClientMustNotExist:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("unresolved bare names must not dispatch")

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr("app.services.mcp_client.MCPClient", ClientMustNotExist)

    outcome = await agent_tools._execute_mcp_tool_outcome(
        "lookup", {}, agent_id=uuid.uuid4()
    )
    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_tool_not_available"


@pytest.mark.asyncio
async def test_durable_dispatcher_selects_native_mcp_outcome_only_after_exact_resolution(
    monkeypatch,
) -> None:
    expected_agent_id = uuid.uuid4()
    target = {
        "full_name": "mcp_server_lookup",
        "raw_name": "lookup",
        "server_url": "https://mcp.example/server",
        "server_name": "server",
        "config": {},
    }

    async def resolve(tool_name, resolved_agent_id, **kwargs):
        assert tool_name == target["full_name"]
        assert resolved_agent_id == expected_agent_id
        assert kwargs == {}
        return target

    async def execute(resolved, arguments, *, agent_id):
        assert resolved is target
        assert arguments == {"q": "x"}
        assert agent_id == expected_agent_id
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="ok",
            result_ref=None,
        )

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr(
        agent_tools,
        "_execute_resolved_mcp_target_outcome",
        execute,
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        target["full_name"],
        {"q": "x"},
        agent_id=expected_agent_id,
        user_id=uuid.uuid4(),
    )
    assert outcome.status == "succeeded"


@pytest.mark.asyncio
async def test_legacy_mcp_consumer_still_receives_text_wrapper(monkeypatch) -> None:
    target = {
        "full_name": "mcp_server_lookup",
        "raw_name": "lookup",
        "server_url": "https://mcp.example/server",
        "server_name": "server",
        "config": {},
    }

    async def resolve(tool_name, _agent_id, *, allow_legacy_bare_name=False):
        assert tool_name == "lookup"
        assert allow_legacy_bare_name is True
        return target

    async def execute(_target, _arguments, *, agent_id):
        assert agent_id is not None
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="legacy result",
            result_ref=None,
        )

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr(
        agent_tools,
        "_execute_resolved_mcp_target_outcome",
        execute,
    )

    result = await agent_tools._execute_mcp_tool(
        "lookup", {}, agent_id=uuid.uuid4()
    )
    assert result == "✅ legacy result"


@pytest.mark.parametrize(
    ("response", "status", "error_code"),
    [
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"message": "denied"}},
            "failed",
            "mcp_provider_rejected",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "tool rejected"}],
                },
            },
            "failed",
            "mcp_tool_error",
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}},
            "failed",
            "mcp_tool_error",
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "result": {"unexpected": True}},
            "unknown",
            "mcp_malformed_response",
        ),
    ],
)
def test_mcp_response_status_is_derived_from_protocol_facts(
    response,
    status,
    error_code,
) -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        response,
        full_tool_name="mcp_server_lookup",
    )
    assert outcome.status == status
    assert outcome.error_code == error_code


def test_mcp_structured_content_is_preserved_and_secret_safe() -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "api_key=super-secret"}],
                "structuredContent": {
                    "answer": 42,
                    "access_token": "token-secret",
                },
            },
        },
        full_tool_name="mcp_server_lookup",
    )

    assert outcome.status == "succeeded"
    assert outcome.metadata["structured_content"] == {
        "answer": 42,
        "access_token": "[REDACTED]",
    }
    serialized = json.dumps(
        {"summary": outcome.result_summary, "metadata": outcome.metadata}
    )
    assert "super-secret" not in serialized
    assert "token-secret" not in serialized


def test_configured_async_mcp_pending_returns_pollable_non_terminal_outcome() -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": '{"status":"downloading","message":"queued"}',
                    }
                ]
            },
        },
        full_tool_name="arxiv_local-download_paper",
        arguments={"paper_id": "2501.01234"},
        async_completion=_async_completion_contract(),
    )

    assert outcome.status == "pending"
    assert outcome.error_code is None
    assert outcome.metadata["runtime_async_pending"] is True
    assert outcome.metadata["async_operation"]["operation_id"] == "2501.01234"
    assert outcome.metadata["async_operation"]["state"] == "downloading"
    assert outcome.metadata["async_operation"]["poll"] == {
        "tool": "arxiv_local-download_paper",
        "arguments": {"paper_id": "2501.01234", "check_status": True},
        "interval_ms": 1000,
    }
    assert "check_status" in (outcome.result_summary or "")


@pytest.mark.asyncio
async def test_resolved_mcp_applies_trusted_async_completion_contract(
    monkeypatch,
) -> None:
    async def raw_call(self, raw_name, arguments):
        del self
        assert raw_name == "download_paper"
        assert arguments == {"paper_id": "2501.01234"}
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": '{"status":"downloading"}'}
                ]
            },
        }

    monkeypatch.setattr(MCPClient, "call_tool_result", raw_call)
    outcome = await agent_tools._execute_resolved_mcp_target_outcome(
        {
            "full_name": "arxiv_local-download_paper",
            "raw_name": "download_paper",
            "server_url": "https://arxiv.example/mcp",
            "server_name": "arxiv-local",
            "config": {},
            "async_completion": _async_completion_contract(),
        },
        {"paper_id": "2501.01234"},
        agent_id=uuid.uuid4(),
    )

    assert outcome.status == "pending"
    assert outcome.metadata["runtime_async_pending"] is True


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_error"),
    [
        ("success", "succeeded", None),
        ("error", "failed", "mcp_async_operation_failed"),
        ("unknown", "unknown", "mcp_async_operation_unknown"),
    ],
)
def test_configured_async_mcp_maps_declared_terminal_states(
    provider_status: str,
    expected_status: str,
    expected_error: str | None,
) -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"status": provider_status}),
                    }
                ]
            },
        },
        full_tool_name="arxiv_local-download_paper",
        arguments={"paper_id": "2501.01234", "check_status": True},
        async_completion=_async_completion_contract(),
    )

    assert outcome.status == expected_status
    assert outcome.error_code == expected_error
    assert outcome.metadata["runtime_async_pending"] is False
    assert outcome.metadata["async_operation"]["state"] == provider_status


def test_configured_async_mcp_parses_terminal_failure_before_generic_is_error() -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": '{"status":"error","message":"conversion failed"}',
                    }
                ],
            },
        },
        full_tool_name="arxiv_local-download_paper",
        arguments={"paper_id": "2501.01234", "check_status": True},
        async_completion=_async_completion_contract(),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_async_operation_failed"
    assert outcome.metadata["runtime_async_pending"] is False
    assert outcome.metadata["async_operation"]["operation_id"] == "2501.01234"


def test_unconfigured_mcp_never_guesses_pending_state_from_text() -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": '{"status":"downloading"}'}
                ]
            },
        },
        full_tool_name="mcp_unconfigured",
    )

    assert outcome.status == "succeeded"
    assert "runtime_async_pending" not in outcome.metadata


@pytest.mark.parametrize(
    ("arguments", "text"),
    [
        ({}, '{"status":"downloading"}'),
        ({"paper_id": "2501.01234"}, "not json"),
        ({"paper_id": "2501.01234"}, '{"message":"missing status"}'),
        ({"paper_id": "2501.01234"}, '{"status":"surprise"}'),
    ],
)
def test_configured_async_mcp_malformed_or_unclassified_fails_closed(
    arguments: dict,
    text: str,
) -> None:
    outcome = agent_tools._mcp_call_response_outcome(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": text}]},
        },
        full_tool_name="arxiv_local-download_paper",
        arguments=arguments,
        async_completion=_async_completion_contract(),
    )

    assert outcome.status == "unknown"
    assert outcome.error_code in {
        "mcp_async_contract_invalid",
        "mcp_async_operation_unknown",
    }


@pytest.mark.asyncio
async def test_transport_detection_uses_read_only_probe_before_one_business_call(
    monkeypatch,
) -> None:
    client = MCPClient("https://mcp.example/server")
    calls: list[tuple[str, str]] = []

    async def streamable(method, _params=None):
        calls.append(("streamable", method))
        if method == "tools/list":
            return {"result": {"tools": []}}
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}

    async def sse(method, _params=None):
        calls.append(("sse", method))
        raise AssertionError("a successful read-only probe selected streamable")

    monkeypatch.setattr(client, "_streamable_request", streamable)
    monkeypatch.setattr(client, "_sse_request", sse)

    result = await client.call_tool_result("lookup", {"q": "x"})

    assert result["result"]["content"][0]["text"] == "ok"
    assert calls == [
        ("streamable", "tools/list"),
        ("streamable", "tools/call"),
    ]


@pytest.mark.asyncio
async def test_post_dispatch_timeout_never_replays_business_call(monkeypatch) -> None:
    client = MCPClient("https://mcp.example/server")
    business_calls = 0
    sse_business_calls = 0

    async def streamable(method, _params=None):
        nonlocal business_calls
        if method == "tools/list":
            return {"result": {"tools": []}}
        business_calls += 1
        raise httpx.ReadTimeout("response lost after dispatch")

    async def sse(method, _params=None):
        nonlocal sse_business_calls
        if method == "tools/call":
            sse_business_calls += 1
        return {"result": {"tools": []}}

    monkeypatch.setattr(client, "_streamable_request", streamable)
    monkeypatch.setattr(client, "_sse_request", sse)

    with pytest.raises(httpx.ReadTimeout):
        await client.call_tool_result("lookup", {"q": "x"})

    assert business_calls == 1
    assert sse_business_calls == 0


@pytest.mark.asyncio
async def test_direct_mcp_malformed_or_lost_response_is_unknown(monkeypatch) -> None:
    target = {
        "full_name": "mcp_server_lookup",
        "raw_name": "lookup",
        "server_url": "https://mcp.example/server",
        "server_name": "server",
        "config": {},
    }

    async def resolve(_tool_name, _agent_id, *, allow_legacy_bare_name=False):
        assert allow_legacy_bare_name is False
        return target

    async def malformed(_self, _raw_name, _arguments):
        return {"jsonrpc": "2.0", "id": 1, "result": {"bad": True}}

    monkeypatch.setattr(agent_tools, "_resolve_mcp_execution_target", resolve)
    monkeypatch.setattr(MCPClient, "call_tool_result", malformed)
    malformed_outcome = await agent_tools._execute_mcp_tool_outcome(
        target["full_name"], {}, agent_id=uuid.uuid4()
    )
    assert malformed_outcome.status == "unknown"

    async def disconnected(_self, _raw_name, _arguments):
        raise httpx.ReadTimeout("api_key=must-not-leak")

    monkeypatch.setattr(MCPClient, "call_tool_result", disconnected)
    disconnected_outcome = await agent_tools._execute_mcp_tool_outcome(
        target["full_name"], {}, agent_id=uuid.uuid4()
    )
    assert disconnected_outcome.status == "unknown"
    assert "must-not-leak" not in (disconnected_outcome.result_summary or "")


class _SmitheryResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _SmitheryClient:
    calls = 0
    response = _SmitheryResponse(401, "api_key=response-secret")

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        return type(self).response


@pytest.mark.asyncio
async def test_smithery_auth_recovery_is_failed_and_hides_url_and_credentials(
    monkeypatch,
) -> None:
    async def smithery_key(_agent_id):
        return "smithery-secret"

    async def recover(*_args, **_kwargs):
        return (
            "Re-authorization needed: "
            "https://smithery.example/setup?apiKey=url-secret"
        )

    monkeypatch.setattr(
        "app.services.resource_discovery._get_smithery_api_key",
        smithery_key,
    )
    monkeypatch.setattr(agent_tools, "_smithery_auto_recover", recover)
    monkeypatch.setattr(httpx, "AsyncClient", _SmitheryClient)
    _SmitheryClient.calls = 0
    _SmitheryClient.response = _SmitheryResponse(
        401,
        "api_key=response-secret",
    )

    outcome = await agent_tools._execute_via_smithery_connect_outcome(
        "https://example.run.tools",
        "lookup",
        {},
        {
            "smithery_namespace": "namespace",
            "smithery_connection_id": "connection",
        },
        agent_id=uuid.uuid4(),
        full_tool_name="mcp_example_lookup",
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_auth_required"
    assert _SmitheryClient.calls == 1
    serialized = json.dumps(
        {"summary": outcome.result_summary, "metadata": outcome.metadata}
    )
    assert "smithery.example" not in serialized
    assert "url-secret" not in serialized
    assert "smithery-secret" not in serialized
    assert "response-secret" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "rejected"}],
                },
            },
            "failed",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"structuredContent": {"answer": 42}},
            },
            "succeeded",
        ),
        ({"jsonrpc": "2.0", "id": 1, "result": {"bad": True}}, "unknown"),
    ],
)
async def test_smithery_uses_the_same_protocol_outcome_semantics(
    monkeypatch,
    payload,
    expected_status,
) -> None:
    async def smithery_key(_agent_id):
        return "smithery-secret"

    monkeypatch.setattr(
        "app.services.resource_discovery._get_smithery_api_key",
        smithery_key,
    )
    monkeypatch.setattr(httpx, "AsyncClient", _SmitheryClient)
    _SmitheryClient.calls = 0
    _SmitheryClient.response = _SmitheryResponse(200, json.dumps(payload))

    outcome = await agent_tools._execute_via_smithery_connect_outcome(
        "https://example.run.tools",
        "lookup",
        {},
        {
            "smithery_namespace": "namespace",
            "smithery_connection_id": "connection",
        },
        agent_id=uuid.uuid4(),
        full_tool_name="mcp_example_lookup",
    )

    assert outcome.status == expected_status
    assert _SmitheryClient.calls == 1
