"""D-020 F4 contracts for the remaining Feishu reads and approval create."""

from __future__ import annotations

from collections import defaultdict
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import activity_logger, agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_policy,
    builtin_readiness,
    builtin_sensitive_paths,
)
from app.services.feishu_service import feishu_service


F4_READ_TOOLS = frozenset(
    {
        "feishu_user_search",
        "feishu_approval_query",
        "feishu_approval_get",
    }
)
APPROVAL_CREATE = "feishu_approval_create"


@pytest.fixture(autouse=True)
def isolate_activity_log(monkeypatch) -> None:
    """Keep every F4 red test on local fakes, including legacy fallbacks."""

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(activity_logger, "log_activity", no_activity)


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeHTTP:
    def __init__(self) -> None:
        self.responses: dict[str, list[object]] = defaultdict(list)
        self.calls: list[tuple[str, str, dict]] = []

    def add(self, method: str, *responses: object) -> None:
        self.responses[method].extend(responses)

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses[method]:
            raise AssertionError(
                f"unexpected or replayed {method.upper()} request: {url}"
            )
        response = self.responses[method].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def calls_for(self, method: str) -> list[tuple[str, str, dict]]:
        return [call for call in self.calls if call[0] == method]


class FakeDBContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_args):
        return False


def install_feishu_provider(monkeypatch, transport: FakeHTTP) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            return await transport.request("get", url, **kwargs)

        async def post(self, url, **kwargs):
            return await transport.request("post", url, **kwargs)

    async def credentials(_agent_id):
        return "app-id", "app-secret"

    async def tenant_token(_app_id, _app_secret):
        return "tenant-token"

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    monkeypatch.setattr(
        feishu_service,
        "get_tenant_access_token",
        tenant_token,
    )


def install_directory_payload(
    monkeypatch,
    payload: dict,
) -> list[tuple[uuid.UUID, dict]]:
    calls: list[tuple[uuid.UUID, dict]] = []

    async def query_directory(agent_id: uuid.UUID, arguments: dict) -> dict:
        calls.append((agent_id, dict(arguments)))
        return payload

    async def legacy_search(_agent_id: uuid.UUID, _arguments: dict) -> str:
        # Prevent the current untyped fallback from touching a real database.
        return "legacy untyped Feishu user search"

    async def credentials(_agent_id):
        return "app-id", "app-secret"

    monkeypatch.setattr(
        agent_tools,
        "_query_directory_payload",
        query_directory,
    )
    monkeypatch.setattr(agent_tools, "_feishu_user_search", legacy_search)
    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    return calls


def install_create_target(
    monkeypatch,
    *,
    target_member_id: uuid.UUID,
    provider_type: str = "feishu",
    provider_user_id: str = "user-applicant",
) -> dict[str, list]:
    captured: dict[str, list] = {
        "resolver": [],
        "directory": [],
    }
    target = SimpleNamespace(
        member=SimpleNamespace(
            id=target_member_id,
            external_id=provider_user_id,
            open_id="ou-should-not-be-used",
        ),
        provider=SimpleNamespace(provider_type=provider_type),
        provider_type=provider_type,
    )

    async def resolve(_db, agent_id, **kwargs):
        captured["resolver"].append((agent_id, dict(kwargs)))
        return target, None

    async def query_directory(agent_id, arguments):
        captured["directory"].append((agent_id, dict(arguments)))
        return {
            "ok": True,
            "members": [
                {
                    "member_type": "human",
                    "target_member_id": str(target_member_id),
                    "display_name": "Applicant",
                    "can_contact": True,
                    "provider": {
                        "provider_type": provider_type,
                        "external_id": provider_user_id,
                        "open_id": "ou-should-not-be-used",
                    },
                }
            ],
        }

    monkeypatch.setattr(agent_tools, "async_session", lambda: FakeDBContext())
    monkeypatch.setattr(agent_tools, "_resolve_roster_human_target", resolve)
    monkeypatch.setattr(agent_tools, "_query_directory_payload", query_directory)
    return captured


async def execute(
    tool_name: str,
    arguments: dict,
    *,
    agent_id: uuid.UUID | None = None,
):
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=agent_id or uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


async def execute_hidden_create(
    arguments: dict,
    *,
    agent_id: uuid.UUID | None = None,
) -> ToolExecutionOutcome:
    adapter = getattr(agent_tools, "_feishu_approval_create_outcome", None)
    assert callable(adapter), (
        "feishu_approval_create needs a typed adapter before its confirmation "
        "gate can expose it"
    )
    return await adapter(agent_id or uuid.uuid4(), arguments)


def assert_outcome(value: object, status: str) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


def schema_for(tool_name: str) -> dict:
    return builtin_model_definition(tool_name)["function"]["parameters"]


def approval_query_arguments() -> dict:
    return {
        "approval_code": "approval-definition-1",
        "instance_status": "PENDING",
        "page_size": 20,
        "page_token": "page-in",
    }


def approval_get_arguments() -> dict:
    return {
        "instance_id": "instance-1",
        "section": "summary",
        "offset": 0,
        "limit": 20,
    }


def queue_read_response(
    transport: FakeHTTP,
    tool_name: str,
    response: object,
) -> None:
    method = "post" if tool_name == "feishu_approval_query" else "get"
    transport.add(method, response)


def read_arguments(tool_name: str) -> dict:
    if tool_name == "feishu_approval_query":
        return approval_query_arguments()
    return approval_get_arguments()


def test_f4_read_tools_have_canonical_read_policy_and_feishu_readiness() -> None:
    for tool_name in F4_READ_TOOLS:
        assert builtin_policy(tool_name) == {
            "effect": "read",
            "retry_policy": "safe",
            "parallel_safe": True,
        }
        assert builtin_readiness(tool_name) == "feishu_channel"


def test_f4_read_tools_are_in_native_typed_workset() -> None:
    assert F4_READ_TOOLS <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_f4_read_visibility_requires_local_feishu_readiness(
    monkeypatch,
) -> None:
    assigned = [builtin_model_definition(name) for name in sorted(F4_READ_TOOLS)]

    async def assigned_tools(_agent_id):
        return assigned

    async def not_ready(_agent_id):
        return False

    async def no_dynamic(_agent_id):
        return set()

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned_tools)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", not_ready)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *F4_READ_TOOLS,
            }
        ),
    )

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_f4_read_visibility_contains_only_ready_assigned_tools(
    monkeypatch,
) -> None:
    assigned_names = {"feishu_user_search", "feishu_approval_get"}
    assigned = [builtin_model_definition(name) for name in sorted(assigned_names)]

    async def assigned_tools(_agent_id):
        return assigned

    async def ready(_agent_id):
        return True

    async def no_dynamic(_agent_id):
        return set()

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned_tools)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", ready)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *F4_READ_TOOLS,
            }
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {tool["function"]["name"] for tool in resolved} == assigned_names


@pytest.mark.asyncio
async def test_approval_create_stays_hidden_until_confirmation_gate_is_wired(
    monkeypatch,
) -> None:
    assigned = [builtin_model_definition(APPROVAL_CREATE)]

    async def assigned_tools(_agent_id):
        return assigned

    async def ready(_agent_id):
        return True

    async def no_dynamic(_agent_id):
        return set()

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned_tools)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", ready)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )

    assert APPROVAL_CREATE not in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


def test_user_search_schema_uses_directory_query_and_bounded_pagination() -> None:
    schema = schema_for("feishu_user_search")

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "limit", "offset"}
    assert schema["properties"]["query"]["minLength"] == 1
    limit = schema["properties"]["limit"]
    assert limit["type"] == "integer"
    assert limit["default"] == 20
    assert limit["minimum"] == 1
    assert limit["maximum"] == 50
    offset = schema["properties"]["offset"]
    assert offset["type"] == "integer"
    assert offset["default"] == 0
    assert offset["minimum"] == 0


def test_approval_query_schema_uses_provider_names_and_pagination() -> None:
    schema = schema_for("feishu_approval_query")

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["approval_code"]
    assert set(schema["properties"]) == {
        "approval_code",
        "instance_status",
        "page_size",
        "page_token",
    }
    assert "status" not in schema["properties"]
    assert schema["properties"]["page_size"]["minimum"] == 1
    assert schema["properties"]["page_size"]["maximum"] == 100


def test_approval_get_schema_selects_one_bounded_section() -> None:
    schema = schema_for("feishu_approval_get")

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["instance_id"]
    assert set(schema["properties"]) == {
        "instance_id",
        "section",
        "offset",
        "limit",
    }
    assert schema["properties"]["section"]["default"] == "summary"
    assert set(schema["properties"]["section"]["enum"]) == {
        "summary",
        "form",
        "tasks",
        "timeline",
        "comments",
    }
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 50
    assert schema["properties"]["offset"]["minimum"] == 0


def test_approval_create_schema_uses_stable_member_id_and_sensitive_form() -> None:
    schema = schema_for(APPROVAL_CREATE)

    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "approval_code",
        "target_member_id",
        "form_data",
    ]
    assert set(schema["properties"]) == {
        "approval_code",
        "target_member_id",
        "form_data",
    }
    assert "user_id" not in schema["properties"]
    assert builtin_policy(APPROVAL_CREATE) == {
        "effect": "external_write",
        "retry_policy": "never",
        "parallel_safe": False,
    }
    assert builtin_readiness(APPROVAL_CREATE) == "feishu_channel"
    assert builtin_sensitive_paths(APPROVAL_CREATE) == ("form_data",)


def test_approval_create_form_data_is_redacted_from_observability() -> None:
    sanitized = agent_tools._observability_arguments(
        APPROVAL_CREATE,
        {
            "approval_code": "approval-definition-1",
            "target_member_id": str(uuid.uuid4()),
            "form_data": '[{"id":"reason","value":"secret"}]',
        },
    )

    assert sanitized["form_data"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_user_search_reuses_tenant_scoped_human_directory_window(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    calls = install_directory_payload(
        monkeypatch,
        {
            "ok": True,
            "members": [],
            "has_more": False,
            "limit": 7,
            "offset": 3,
        },
    )

    assert_outcome(
        await execute(
            "feishu_user_search",
            {"query": "Alice", "limit": 7, "offset": 3},
            agent_id=agent_id,
        ),
        "succeeded",
    )

    assert calls == [
        (
            agent_id,
            {
                "query": "Alice",
                "member_type": "human",
                "include_uncontactable": False,
                "limit": 7,
                "offset": 3,
            },
        )
    ]


@pytest.mark.asyncio
async def test_user_search_returns_only_visible_contactable_feishu_members_without_raw_ids(
    monkeypatch,
) -> None:
    wanted_member_id = uuid.uuid4()
    install_directory_payload(
        monkeypatch,
        {
            "ok": True,
            "has_more": False,
            "members": [
                {
                    "member_type": "human",
                    "target_member_id": str(wanted_member_id),
                    "platform_user_id": str(uuid.uuid4()),
                    "display_name": "Alice",
                    "title": "Engineer",
                    "can_contact": True,
                    "provider": {
                        "provider_type": "feishu",
                        "open_id": "ou-private-alice",
                        "external_id": "user-private-alice",
                    },
                    "email": "alice-private@example.com",
                },
                {
                    "member_type": "human",
                    "target_member_id": str(uuid.uuid4()),
                    "display_name": "Teams Alice",
                    "can_contact": True,
                    "provider": {
                        "provider_type": "teams",
                        "external_id": "teams-private-alice",
                    },
                },
                {
                    "member_type": "human",
                    "target_member_id": str(uuid.uuid4()),
                    "display_name": "Hidden Alice",
                    "can_contact": False,
                    "provider": {
                        "provider_type": "feishu",
                        "external_id": "user-private-hidden",
                    },
                },
            ],
        },
    )

    outcome = assert_outcome(
        await execute("feishu_user_search", {"query": "Alice"}),
        "succeeded",
    )
    payload = json.loads(outcome.summary or "")

    assert payload["returned_count"] == 1
    assert payload["members"][0]["target_member_id"] == str(wanted_member_id)
    assert payload["members"][0]["display_name"] == "Alice"
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "platform_user_id",
        "open_id",
        "external_id",
        "email",
        "ou-private-alice",
        "user-private-alice",
        "teams-private-alice",
        "user-private-hidden",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_user_search_directory_failure_is_typed_retryable_read(
    monkeypatch,
) -> None:
    install_directory_payload(
        monkeypatch,
        {
            "ok": False,
            "error": {
                "code": "query_directory_failed",
                "message": "directory unavailable",
            },
        },
    )

    outcome = assert_outcome(
        await execute("feishu_user_search", {"query": "Alice"}),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code == "query_directory_failed"


@pytest.mark.asyncio
async def test_approval_query_uses_instance_status_and_returns_provider_page_facts(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "instance_list": [
                        {
                            "instance": {
                                "code": "instance-1",
                                "status": "pending",
                                "title": "Expense one",
                            }
                        },
                        {
                            "instance": {
                                "code": "instance-2",
                                "status": "pending",
                                "title": "Expense two",
                            }
                        },
                    ],
                    "has_more": True,
                    "page_token": "page-out",
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_approval_query", approval_query_arguments()),
        "succeeded",
    )

    assert "instance-1" in (outcome.summary or "")
    assert "instance-2" in (outcome.summary or "")
    assert outcome.metadata["has_more"] is True
    assert outcome.metadata["page_token"] == "page-out"
    assert outcome.metadata["instance_count"] == 2
    assert len(transport.calls_for("post")) == 1
    _, url, kwargs = transport.calls_for("post")[0]
    assert url.endswith("/approval/v4/instances/query")
    assert kwargs["json"] == {
        "approval_code": "approval-definition-1",
        "instance_status": "PENDING",
    }
    assert kwargs["params"]["page_size"] == 20
    assert kwargs["params"]["page_token"] == "page-in"


@pytest.mark.asyncio
async def test_approval_query_code_zero_empty_page_is_success(monkeypatch) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "instance_list": [],
                    "has_more": False,
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(
            "feishu_approval_query",
            {"approval_code": "approval-definition-1"},
        ),
        "succeeded",
    )

    assert outcome.metadata["instance_count"] == 0
    assert outcome.metadata["has_more"] is False


@pytest.mark.asyncio
async def test_approval_query_rejects_invalid_page_size_before_dispatch(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(
            "feishu_approval_query",
            {"approval_code": "approval-definition-1", "page_size": 101},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "invalid_tool_arguments"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_approval_query_malformed_instance_list_is_retryable_failure(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "instance_list": "not-a-list",
                    "has_more": False,
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_approval_query", approval_query_arguments()),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.asyncio
async def test_approval_get_default_summary_excludes_sensitive_large_sections(
    monkeypatch,
) -> None:
    form_secret = "FORM-PRIVATE-" + "x" * 12000
    comment_secret = "COMMENT-PRIVATE-" + "y" * 12000
    transport = FakeHTTP()
    transport.add(
        "get",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "approval_name": "Expense",
                    "status": "PENDING",
                    "serial_number": "EXP-42",
                    "user_id": "user-private-applicant",
                    "open_id": "ou-private-applicant",
                    "form": json.dumps(
                        [{"id": "reason", "value": form_secret}]
                    ),
                    "task_list": [{"id": "task-private"}],
                    "comment_list": [{"content": comment_secret}],
                    "timeline": [{"type": "START"}],
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_approval_get", {"instance_id": "instance-1"}),
        "succeeded",
    )

    assert outcome.result_ref == "instance-1"
    assert "Expense" in (outcome.summary or "")
    assert "PENDING" in (outcome.summary or "")
    assert len(outcome.summary or "") <= 8192
    for forbidden in (
        form_secret,
        comment_secret,
        "user-private-applicant",
        "ou-private-applicant",
        "task-private",
    ):
        assert forbidden not in (outcome.summary or "")


@pytest.mark.asyncio
async def test_approval_get_returns_only_requested_section_window(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "get",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "approval_name": "Expense",
                    "status": "PENDING",
                    "task_list": [
                        {"id": "task-1", "status": "PENDING"},
                        {"id": "task-2", "status": "PENDING"},
                        {"id": "task-3", "status": "PENDING"},
                    ],
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(
            "feishu_approval_get",
            {
                "instance_id": "instance-1",
                "section": "tasks",
                "offset": 1,
                "limit": 1,
            },
        ),
        "succeeded",
    )

    assert "task-2" in (outcome.summary or "")
    assert "task-1" not in (outcome.summary or "")
    assert "task-3" not in (outcome.summary or "")
    assert outcome.metadata["section"] == "tasks"
    assert outcome.metadata["offset"] == 1
    assert outcome.metadata["returned_count"] == 1
    assert outcome.metadata["has_more"] is True
    assert outcome.metadata["next_offset"] == 2


@pytest.mark.asyncio
async def test_approval_get_rejects_invalid_section_before_dispatch(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(
            "feishu_approval_get",
            {"instance_id": "instance-1", "section": "everything"},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "invalid_tool_arguments"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_approval_get_malformed_data_is_retryable_failure(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add("get", FakeResponse({"code": 0, "data": ["not", "object"]}))
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_approval_get", approval_get_arguments()),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.parametrize("tool_name", sorted(F4_READ_TOOLS - {"feishu_user_search"}))
@pytest.mark.asyncio
async def test_approval_reads_classify_business_rejection_as_nonretryable(
    monkeypatch,
    tool_name,
) -> None:
    transport = FakeHTTP()
    queue_read_response(
        transport,
        tool_name,
        FakeResponse({"code": 99991663, "msg": "permission denied"}),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, read_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.parametrize("tool_name", sorted(F4_READ_TOOLS - {"feishu_user_search"}))
@pytest.mark.asyncio
async def test_approval_reads_classify_http_4xx_as_nonretryable(
    monkeypatch,
    tool_name,
) -> None:
    transport = FakeHTTP()
    queue_read_response(
        transport,
        tool_name,
        FakeResponse(
            {"code": 0, "msg": "bad request"},
            status_code=400,
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, read_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.parametrize("tool_name", sorted(F4_READ_TOOLS - {"feishu_user_search"}))
@pytest.mark.asyncio
async def test_approval_reads_classify_http_5xx_as_retryable(
    monkeypatch,
    tool_name,
) -> None:
    transport = FakeHTTP()
    queue_read_response(
        transport,
        tool_name,
        FakeResponse(
            {"code": 0, "data": {}},
            status_code=503,
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, read_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.parametrize("tool_name", sorted(F4_READ_TOOLS - {"feishu_user_search"}))
@pytest.mark.asyncio
async def test_approval_reads_classify_transport_timeout_as_retryable(
    monkeypatch,
    tool_name,
) -> None:
    transport = FakeHTTP()
    queue_read_response(
        transport,
        tool_name,
        httpx.ReadTimeout("approval read timed out"),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, read_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.parametrize("tool_name", sorted(F4_READ_TOOLS - {"feishu_user_search"}))
@pytest.mark.asyncio
async def test_approval_reads_classify_invalid_json_as_retryable(
    monkeypatch,
    tool_name,
) -> None:
    transport = FakeHTTP()
    queue_read_response(
        transport,
        tool_name,
        FakeResponse(ValueError("provider returned HTML")),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, read_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.asyncio
async def test_approval_create_resolves_stable_member_and_returns_receipt_once(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    form_data = '[{"id":"reason","value":"FORM-PRIVATE-VALUE"}]'
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {"instance_code": "approval-instance-1"},
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)
    captured = install_create_target(
        monkeypatch,
        target_member_id=target_member_id,
    )

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": form_data,
            },
            agent_id=agent_id,
        ),
        "succeeded",
    )

    assert outcome.result_ref == "approval-instance-1"
    assert "FORM-PRIVATE-VALUE" not in (outcome.summary or "")
    assert len(transport.calls_for("post")) == 1
    _, url, kwargs = transport.calls_for("post")[0]
    assert url.endswith("/approval/v4/instances")
    assert kwargs["json"]["approval_code"] == "approval-definition-1"
    assert kwargs["json"]["user_id"] == "user-applicant"
    assert kwargs["json"]["form"] == form_data
    assert "target_member_id" not in kwargs["json"]
    assert captured["resolver"] or captured["directory"]
    if captured["resolver"]:
        resolved_agent_id, resolver_args = captured["resolver"][0]
        assert resolved_agent_id == agent_id
        assert resolver_args["target_member_id"] == str(target_member_id)
        assert resolver_args["provider_type"] == "feishu"
        assert resolver_args["require_provider_identity"] is True


@pytest.mark.asyncio
async def test_approval_create_rejects_non_array_form_before_dispatch(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)
    install_create_target(monkeypatch, target_member_id=target_member_id)

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": '{"not":"an array"}',
            }
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "invalid_tool_arguments"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_approval_create_rejects_non_feishu_member_before_dispatch(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)
    install_create_target(
        monkeypatch,
        target_member_id=target_member_id,
        provider_type="teams",
        provider_user_id="teams-user",
    )

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": "[]",
            }
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert transport.calls == []


@pytest.mark.asyncio
async def test_approval_create_missing_provider_receipt_is_unknown_without_replay(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    transport = FakeHTTP()
    transport.add("post", FakeResponse({"code": 0, "data": {}}))
    install_feishu_provider(monkeypatch, transport)
    install_create_target(monkeypatch, target_member_id=target_member_id)

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": "[]",
            }
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert len(transport.calls_for("post")) == 1


@pytest.mark.asyncio
async def test_approval_create_dispatch_timeout_is_unknown_without_replay(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    transport = FakeHTTP()
    transport.add("post", httpx.ReadTimeout("approval receipt timed out"))
    install_feishu_provider(monkeypatch, transport)
    install_create_target(monkeypatch, target_member_id=target_member_id)

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": "[]",
            }
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert len(transport.calls_for("post")) == 1


@pytest.mark.asyncio
async def test_approval_create_business_rejection_is_failed_without_replay(
    monkeypatch,
) -> None:
    target_member_id = uuid.uuid4()
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 1390001, "msg": "approval rejected"}),
    )
    install_feishu_provider(monkeypatch, transport)
    install_create_target(monkeypatch, target_member_id=target_member_id)

    outcome = assert_outcome(
        await execute_hidden_create(
            {
                "approval_code": "approval-definition-1",
                "target_member_id": str(target_member_id),
                "form_data": "[]",
            }
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert len(transport.calls_for("post")) == 1
