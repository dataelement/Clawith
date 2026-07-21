"""D-020 F2 typed execution contracts for Feishu Bitable tools."""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import activity_logger, agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import builtin_model_definition
from app.services.feishu_service import FeishuAPIError, feishu_service


BASE_URL = "https://tenant.feishu.cn/base/app1?table=table1"

F2_BITABLE_TOOLS = frozenset(
    {
        "bitable_create_app",
        "bitable_list_tables",
        "bitable_list_fields",
        "bitable_query_records",
        "bitable_create_record",
        "bitable_update_record",
        "bitable_delete_record",
    }
)

READ_CASES = (
    (
        "bitable_list_tables",
        "bitable_list_tables",
        {"url": BASE_URL},
    ),
    (
        "bitable_list_fields",
        "bitable_list_fields",
        {"url": BASE_URL},
    ),
    (
        "bitable_query_records",
        "bitable_query_records",
        {"url": BASE_URL, "filter_info": {}, "max_results": 10},
    ),
)

WRITE_CASES = (
    (
        "bitable_create_app",
        "bitable_create_app",
        {"name": "Typed contract app"},
        {
            "code": 0,
            "data": {
                "app": {
                    "app_token": "app-new",
                    "url": "https://tenant.feishu.cn/base/app-new",
                }
            },
        },
        "app-new",
    ),
    (
        "bitable_create_record",
        "bitable_create_record",
        {"url": BASE_URL, "fields": {"Name": "New row"}},
        {
            "code": 0,
            "data": {
                "record": {
                    "record_id": "record-new",
                    "fields": {"Name": "New row"},
                }
            },
        },
        "record-new",
    ),
    (
        "bitable_update_record",
        "bitable_update_record",
        {
            "url": BASE_URL,
            "record_id": "record-update",
            "fields": {"Name": "Updated row"},
        },
        {
            "code": 0,
            "data": {
                "record": {
                    "record_id": "record-update",
                    "fields": {"Name": "Updated row"},
                }
            },
        },
        "record-update",
    ),
    (
        "bitable_delete_record",
        "bitable_delete_record",
        {"url": BASE_URL, "record_id": "record-delete"},
        {"code": 0, "data": {}},
        "record-delete",
    ),
)


class FakeBitableProvider:
    """Local provider fake; every unexpected or repeated dispatch fails."""

    def __init__(self) -> None:
        self.responses: dict[str, list[object]] = defaultdict(list)
        self.calls: dict[str, list[tuple[tuple, dict]]] = defaultdict(list)
        self.enrichment_calls: list[tuple[tuple, dict]] = []
        self.enrichment_error: BaseException | None = None

    def add(self, method: str, *responses: object) -> None:
        self.responses[method].extend(responses)

    def call_count(self, method: str) -> int:
        return len(self.calls[method])

    def _dispatch(self, method: str, args: tuple, kwargs: dict):
        self.calls[method].append((args, kwargs))
        if not self.responses[method]:
            raise AssertionError(f"unexpected or replayed provider call: {method}")
        response = self.responses[method].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def bitable_list_tables(self, *args, **kwargs):
        return self._dispatch("bitable_list_tables", args, kwargs)

    async def bitable_list_fields(self, *args, **kwargs):
        return self._dispatch("bitable_list_fields", args, kwargs)

    async def bitable_query_records(self, *args, **kwargs):
        return self._dispatch("bitable_query_records", args, kwargs)

    async def bitable_create_record(self, *args, **kwargs):
        return self._dispatch("bitable_create_record", args, kwargs)

    async def bitable_update_record(self, *args, **kwargs):
        return self._dispatch("bitable_update_record", args, kwargs)

    async def bitable_delete_record(self, *args, **kwargs):
        return self._dispatch("bitable_delete_record", args, kwargs)

    async def bitable_create_app(self, *args, **kwargs):
        return self._dispatch("bitable_create_app", args, kwargs)


def install_bitable_provider(
    monkeypatch,
    provider: FakeBitableProvider,
) -> None:
    async def credentials(_agent_id):
        return "app-id", "app-secret"

    async def tenant_token(_app_id, _app_secret):
        return "tenant-token"

    async def no_tenant(_agent_id):
        return None

    async def enrich(*args, **kwargs):
        provider.enrichment_calls.append((args, kwargs))
        if provider.enrichment_error is not None:
            raise provider.enrichment_error
        app_token = args[1] if len(args) > 1 else kwargs.get("app_token", "app-1")
        table_id = args[2] if len(args) > 2 else kwargs.get("table_id", "")
        suffix = f"?table={table_id}" if table_id else ""
        return f"https://tenant.feishu.cn/base/{app_token}{suffix}"

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def no_log(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    monkeypatch.setattr(
        feishu_service,
        "get_tenant_access_token",
        tenant_token,
    )
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(agent_tools, "_get_feishu_bitable_url", enrich)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)
    monkeypatch.setattr(
        agent_tools,
        "logger",
        SimpleNamespace(
            debug=no_log,
            info=no_log,
            warning=no_log,
            error=no_log,
            exception=no_log,
        ),
    )
    for method in (
        "bitable_list_tables",
        "bitable_list_fields",
        "bitable_query_records",
        "bitable_create_record",
        "bitable_update_record",
        "bitable_delete_record",
        "bitable_create_app",
    ):
        monkeypatch.setattr(feishu_service, method, getattr(provider, method))


async def execute(tool_name: str, arguments: dict):
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def assert_outcome(value, status: str) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


def empty_page() -> dict:
    return {"code": 0, "data": {"items": []}}


def record(record_id: str) -> dict:
    return {"record_id": record_id, "fields": {"Name": record_id}}


def page(
    *items: dict,
    has_more: bool = False,
    page_token: str | None = None,
) -> dict:
    data = {"items": list(items), "has_more": has_more}
    if page_token is not None:
        data["page_token"] = page_token
    return {"code": 0, "data": data}


def query_filters(call: tuple[tuple, dict]):
    args, kwargs = call
    if "filters" in kwargs:
        return kwargs["filters"]
    if "filter_info" in kwargs:
        return kwargs["filter_info"]
    return args[4] if len(args) > 4 else None


def query_page_size(call: tuple[tuple, dict]) -> int | None:
    args, kwargs = call
    for key in ("page_size", "limit", "max_results"):
        value = kwargs.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for value in args[5:]:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def query_page_token(call: tuple[tuple, dict]) -> str | None:
    args, kwargs = call
    for key in ("page_token", "cursor"):
        value = kwargs.get(key)
        if isinstance(value, str):
            return value
    for value in args[5:]:
        if isinstance(value, str):
            return value
    return None


def test_f2_bitable_tools_are_in_native_typed_workset() -> None:
    assert F2_BITABLE_TOOLS <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_f2_bitable_visibility_requires_local_readiness(monkeypatch) -> None:
    tools = [builtin_model_definition(name) for name in sorted(F2_BITABLE_TOOLS)]

    async def assigned(_agent_id):
        return tools

    async def not_ready(_agent_id):
        return False

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", not_ready)
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *F2_BITABLE_TOOLS}
        ),
    )

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_f2_bitable_visibility_contains_only_ready_assigned_tools(
    monkeypatch,
) -> None:
    assigned_names = {
        "bitable_list_tables",
        "bitable_query_records",
        "bitable_update_record",
    }
    tools = [builtin_model_definition(name) for name in sorted(assigned_names)]

    async def assigned(_agent_id):
        return tools

    async def ready(_agent_id):
        return True

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_agent_has_feishu", ready)
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *F2_BITABLE_TOOLS}
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {item["function"]["name"] for item in resolved} == assigned_names


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments"),
    READ_CASES,
)
@pytest.mark.asyncio
async def test_bitable_reads_accept_code_zero_empty_results(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, empty_page())
    install_bitable_provider(monkeypatch, provider)

    assert_outcome(await execute(tool_name, arguments), "succeeded")


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments"),
    READ_CASES,
)
@pytest.mark.asyncio
async def test_bitable_reads_reject_provider_business_errors(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
) -> None:
    provider = FakeBitableProvider()
    provider.add(
        provider_method,
        {"code": 1254001, "msg": "Bitable rejected the read"},
    )
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is False
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments"),
    READ_CASES,
)
@pytest.mark.asyncio
async def test_bitable_http_failures_are_typed_retryable_reads(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
) -> None:
    provider = FakeBitableProvider()
    provider.add(
        provider_method,
        FeishuAPIError(
            stage=provider_method,
            http_status=503,
            msg="Bitable temporarily unavailable",
        ),
    )
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is True
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments"),
    READ_CASES,
)
@pytest.mark.asyncio
async def test_bitable_read_timeouts_are_retryable_failures(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, httpx.ReadTimeout("Bitable read timed out"))
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is True
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments"),
    READ_CASES,
)
@pytest.mark.asyncio
async def test_bitable_reads_fail_closed_on_malformed_success_payloads(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, {"code": 0, "data": {"items": "not-a-list"}})
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.asyncio
async def test_bitable_query_passes_structured_filter_object_unchanged(
    monkeypatch,
) -> None:
    provider = FakeBitableProvider()
    provider.add("bitable_query_records", empty_page())
    install_bitable_provider(monkeypatch, provider)
    filter_info = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "Status", "operator": "is", "value": ["Open"]}
        ],
    }

    assert_outcome(
        await execute(
            "bitable_query_records",
            {"url": BASE_URL, "filter_info": filter_info, "max_results": 5},
        ),
        "succeeded",
    )

    assert query_filters(provider.calls["bitable_query_records"][0]) == filter_info


@pytest.mark.asyncio
async def test_bitable_query_rejects_invalid_filter_before_provider_dispatch(
    monkeypatch,
) -> None:
    provider = FakeBitableProvider()
    install_bitable_provider(monkeypatch, provider)

    outcome = await execute(
        "bitable_query_records",
        {"url": BASE_URL, "filter_info": "not-json", "max_results": 5},
    )

    assert provider.call_count("bitable_query_records") == 0
    typed = assert_outcome(outcome, "failed")
    assert typed.retryable is False
    assert typed.error_code
    assert typed.error_code != "untyped_tool_outcome"


@pytest.mark.asyncio
async def test_bitable_query_pages_only_to_bounded_max_results(monkeypatch) -> None:
    provider = FakeBitableProvider()
    provider.add(
        "bitable_query_records",
        page(
            record("record-1"),
            record("record-2"),
            has_more=True,
            page_token="next-1",
        ),
        page(record("record-3"), record("record-4")),
    )
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "bitable_query_records",
            {"url": BASE_URL, "filter_info": {}, "max_results": 3},
        ),
        "succeeded",
    )

    assert provider.call_count("bitable_query_records") == 2
    first_call, second_call = provider.calls["bitable_query_records"]
    assert query_page_size(first_call) is not None
    assert 0 < query_page_size(first_call) <= 3
    assert query_page_token(second_call) == "next-1"
    assert query_page_size(second_call) is not None
    assert 0 < query_page_size(second_call) <= 1
    assert "record-1" in (outcome.summary or "")
    assert "record-2" in (outcome.summary or "")
    assert "record-3" in (outcome.summary or "")
    assert "record-4" not in (outcome.summary or "")


@pytest.mark.parametrize(
    (
        "tool_name",
        "provider_method",
        "arguments",
        "provider_response",
        "receipt",
    ),
    WRITE_CASES,
)
@pytest.mark.asyncio
async def test_bitable_writes_return_stable_receipts(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
    provider_response,
    receipt,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, provider_response)
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "succeeded")

    assert outcome.result_ref == receipt
    assert provider.call_count(provider_method) == 1


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments", "provider_response"),
    (
        (
            "bitable_create_app",
            "bitable_create_app",
            {"name": "Missing receipt app"},
            {"code": 0, "data": {"app": {}}},
        ),
        (
            "bitable_create_record",
            "bitable_create_record",
            {"url": BASE_URL, "fields": {"Name": "Missing receipt"}},
            {"code": 0, "data": {"record": {"fields": {}}}},
        ),
        (
            "bitable_update_record",
            "bitable_update_record",
            {
                "url": BASE_URL,
                "record_id": "record-update",
                "fields": {"Name": "Missing receipt"},
            },
            {"code": 0, "data": {"record": {"fields": {}}}},
        ),
    ),
)
@pytest.mark.asyncio
async def test_bitable_code_zero_without_required_receipt_is_unknown(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
    provider_response,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, provider_response)
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "unknown")

    assert outcome.retryable is False
    assert outcome.error_code
    assert provider.call_count(provider_method) == 1


@pytest.mark.asyncio
async def test_bitable_update_requires_requested_and_returned_record_id_to_match(
    monkeypatch,
) -> None:
    provider = FakeBitableProvider()
    provider.add(
        "bitable_update_record",
        {
            "code": 0,
            "data": {"record": {"record_id": "different-record", "fields": {}}},
        },
    )
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "bitable_update_record",
            {
                "url": BASE_URL,
                "record_id": "requested-record",
                "fields": {"Name": "Mismatch"},
            },
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert provider.call_count("bitable_update_record") == 1


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments", "_response", "_receipt"),
    WRITE_CASES,
)
@pytest.mark.asyncio
async def test_bitable_write_business_rejection_is_failed_without_replay(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
    _response,
    _receipt,
) -> None:
    provider = FakeBitableProvider()
    provider.add(
        provider_method,
        {"code": 1254002, "msg": "Bitable rejected the write"},
    )
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is False
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"
    assert provider.call_count(provider_method) == 1


@pytest.mark.parametrize(
    ("tool_name", "provider_method", "arguments", "_response", "_receipt"),
    WRITE_CASES,
)
@pytest.mark.asyncio
async def test_bitable_write_dispatch_timeout_is_unknown_and_never_replayed(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
    _response,
    _receipt,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, httpx.ReadTimeout("write receipt timed out"))
    install_bitable_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "unknown")

    assert outcome.retryable is False
    assert outcome.error_code
    assert provider.call_count(provider_method) == 1


@pytest.mark.parametrize(
    (
        "tool_name",
        "provider_method",
        "arguments",
        "provider_response",
        "_receipt",
    ),
    (
        (
            "bitable_list_tables",
            "bitable_list_tables",
            {"url": BASE_URL},
            {
                "code": 0,
                "data": {"items": [{"table_id": "table-1", "name": "Table"}]},
            },
            None,
        ),
        (
            "bitable_create_app",
            "bitable_create_app",
            {"name": "Enrichment independent app"},
            {"code": 0, "data": {"app": {"app_token": "app-new"}}},
            "app-new",
        ),
        *WRITE_CASES[1:],
    ),
)
@pytest.mark.asyncio
async def test_bitable_url_enrichment_failure_does_not_override_provider_fact(
    monkeypatch,
    tool_name,
    provider_method,
    arguments,
    provider_response,
    _receipt,
) -> None:
    provider = FakeBitableProvider()
    provider.add(provider_method, provider_response)
    provider.enrichment_error = RuntimeError("tenant domain lookup unavailable")
    install_bitable_provider(monkeypatch, provider)

    assert_outcome(await execute(tool_name, arguments), "succeeded")
    assert provider.call_count(provider_method) == 1
