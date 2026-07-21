"""D-020 F0 contracts for Feishu readiness and canonical tool schemas.

These tests intentionally describe the boundary before the production
implementation is changed.  Provider execution outcomes are covered by later
Calendar/Wiki/Bitable batches; F0 only locks local readiness and input
contracts.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services.builtin_tool_definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    builtin_model_definition,
)
from app.services.feishu_service import FeishuAPIError, FeishuService


class _ScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ListResult:
    def __init__(self, values) -> None:
        self._values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _QueuedDB:
    def __init__(self, responses) -> None:
        self._responses = list(responses)

    async def execute(self, _statement):
        if not self._responses:
            raise AssertionError("unexpected database query")
        return self._responses.pop(0)


def _install_session(monkeypatch, db) -> None:
    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(agent_tools, "async_session", session)


def _builtin_row(name: str, *, is_default: bool):
    definition = next(
        item for item in BUILTIN_TOOL_DEFINITIONS if item["name"] == name
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        description=definition["description"],
        category=definition["category"],
        is_default=is_default,
        parameters_schema=definition["parameters_schema"],
        config=definition.get("config", {}),
        source="builtin",
        enabled=True,
    )


def _install_tool_selection_context(
    monkeypatch,
    *,
    target_assignment,
) -> str:
    target = _builtin_row("feishu_calendar_list", is_default=False)
    core = _builtin_row("read_file", is_default=True)
    assignments = []
    if target_assignment is not None:
        assignments.append(
            SimpleNamespace(tool_id=target.id, enabled=target_assignment)
        )

    db = _QueuedDB(
        [
            _ScalarResult(
                SimpleNamespace(tenant_id=uuid.uuid4(), is_system=False)
            ),
            _ListResult(assignments),
            _ListResult([core, target]),
        ]
    )
    _install_session(monkeypatch, db)

    async def has_feishu(_agent_id):
        return True

    async def has_any_channel(_agent_id):
        return False

    async def no_computer(_agent_id):
        return None

    monkeypatch.setattr(agent_tools, "_agent_has_feishu", has_feishu)
    monkeypatch.setattr(agent_tools, "_agent_has_any_channel", has_any_channel)
    monkeypatch.setattr(agent_tools, "_get_computer_os_type", no_computer)
    return target.name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel_config, expected",
    [
        (None, False),
        (
            SimpleNamespace(
                is_configured=True,
                app_id=None,
                app_secret="secret",
            ),
            False,
        ),
        (
            SimpleNamespace(
                is_configured=True,
                app_id="app",
                app_secret=None,
            ),
            False,
        ),
        (
            SimpleNamespace(
                is_configured=True,
                app_id="app",
                app_secret="secret",
            ),
            True,
        ),
    ],
    ids=["missing-row", "missing-app-id", "missing-secret", "complete"],
)
async def test_feishu_local_readiness_requires_complete_channel_credentials(
    monkeypatch,
    channel_config,
    expected,
) -> None:
    _install_session(monkeypatch, _QueuedDB([_ScalarResult(channel_config)]))

    assert await agent_tools._agent_has_feishu(uuid.uuid4()) is expected


@pytest.mark.asyncio
async def test_runtime_resolver_hides_feishu_tool_when_local_readiness_fails(
    monkeypatch,
) -> None:
    tool = builtin_model_definition("feishu_calendar_list")

    async def assigned(_agent_id):
        return [tool]

    async def not_ready(_agent_id):
        return False

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", not_ready)
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                "feishu_calendar_list",
            }
        ),
    )

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_runtime_resolver_never_health_pings_feishu_provider(
    monkeypatch,
) -> None:
    tool = builtin_model_definition("feishu_calendar_list")

    async def assigned(_agent_id):
        return [tool]

    async def ready(_agent_id):
        return True

    class NetworkMustNotBeUsed:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("Runtime readiness must not ping Feishu")

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", ready)
    monkeypatch.setattr(httpx, "AsyncClient", NetworkMustNotBeUsed)
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                "feishu_calendar_list",
            }
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert [item["function"]["name"] for item in resolved] == [
        "feishu_calendar_list"
    ]


@pytest.mark.asyncio
async def test_unassigned_non_default_feishu_tool_is_not_enabled_by_channel(
    monkeypatch,
) -> None:
    target_name = _install_tool_selection_context(
        monkeypatch,
        target_assignment=None,
    )

    tools = await agent_tools.get_agent_tools_for_llm(uuid.uuid4())

    assert target_name not in {
        item["function"]["name"] for item in tools
    }


@pytest.mark.asyncio
async def test_explicitly_disabled_feishu_tool_stays_hidden(
    monkeypatch,
) -> None:
    target_name = _install_tool_selection_context(
        monkeypatch,
        target_assignment=False,
    )

    tools = await agent_tools.get_agent_tools_for_llm(uuid.uuid4())

    assert target_name not in {
        item["function"]["name"] for item in tools
    }


def test_feishu_wiki_list_has_one_canonical_definition() -> None:
    matches = [
        item
        for item in BUILTIN_TOOL_DEFINITIONS
        if item["name"] == "feishu_wiki_list"
    ]

    assert len(matches) == 1


def test_feishu_wiki_list_schema_matches_handler_contract() -> None:
    matches = [
        item
        for item in BUILTIN_TOOL_DEFINITIONS
        if item["name"] == "feishu_wiki_list"
    ]
    assert len(matches) == 1
    schema = matches[0]["parameters_schema"]

    assert schema["required"] == ["node_token"]
    assert schema["properties"]["node_token"]["type"] == "string"
    assert schema["properties"]["recursive"]["type"] == "boolean"
    assert schema["additionalProperties"] is False


def test_send_feishu_message_legacy_schema_matches_compatibility_handler() -> None:
    schema = builtin_model_definition("send_feishu_message")["function"][
        "parameters"
    ]

    assert set(schema["properties"]) == {"target_member_id", "message"}
    assert schema["required"] == ["target_member_id", "message"]
    assert schema["additionalProperties"] is False


def test_send_feishu_message_remains_hidden_from_model_workset() -> None:
    model_names = {
        item["function"]["name"] for item in agent_tools.AGENT_TOOLS
    }

    assert "send_feishu_message" not in model_names


@pytest.mark.parametrize(
    "tool_name",
    ["feishu_calendar_update", "feishu_calendar_delete"],
)
def test_calendar_mutation_contract_requires_event_id_not_user_email(
    tool_name,
) -> None:
    schema = builtin_model_definition(tool_name)["function"]["parameters"]

    assert "user_email" not in schema["properties"]
    assert schema["required"] == ["event_id"]


@pytest.mark.parametrize(
    "tool_name",
    ["bitable_create_record", "bitable_update_record"],
)
def test_bitable_record_fields_use_structured_object_schema(tool_name) -> None:
    schema = builtin_model_definition(tool_name)["function"]["parameters"]

    assert schema["properties"]["fields"]["type"] == "object"


def test_bitable_query_filter_uses_structured_object_schema() -> None:
    schema = builtin_model_definition("bitable_query_records")["function"][
        "parameters"
    ]

    assert schema["properties"]["filter_info"]["type"] == "object"


def test_feishu_response_parser_rejects_non_success_http_status() -> None:
    response = httpx.Response(
        403,
        json={"code": 0, "msg": "unexpected success payload"},
    )

    with pytest.raises(FeishuAPIError) as error:
        FeishuService._parse_api_response(response, stage="calendar_list")

    assert error.value.http_status == 403


def test_feishu_response_parser_rejects_nonzero_business_code() -> None:
    response = httpx.Response(
        200,
        json={"code": 99991672, "msg": "provider rejected request"},
    )

    with pytest.raises(FeishuAPIError) as error:
        FeishuService._parse_api_response(response, stage="calendar_list")

    assert error.value.code == 99991672


def test_feishu_response_parser_accepts_http_success_with_zero_business_code() -> None:
    payload = {"code": 0, "data": {"items": []}}
    response = httpx.Response(200, json=payload)

    assert FeishuService._parse_api_response(
        response,
        stage="calendar_list",
    ) == payload
