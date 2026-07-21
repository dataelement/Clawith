"""D-020 F1 typed execution contracts for the Feishu Bot calendar."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services import activity_logger
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import builtin_model_definition
from app.services.feishu_service import feishu_service


F1_FEISHU_TOOLS = {
    "feishu_calendar_list",
    "feishu_calendar_create",
    "feishu_calendar_update",
    "feishu_calendar_delete",
    "feishu_wiki_list",
}


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self) -> None:
        self.responses = {
            "get": [],
            "post": [],
            "patch": [],
            "delete": [],
        }
        self.calls = []

    def add(self, method: str, *responses) -> None:
        self.responses[method].extend(responses)

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses[method]:
            raise AssertionError(f"unexpected {method.upper()} request: {url}")
        response = self.responses[method].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def install_http(monkeypatch, transport: FakeHTTP) -> None:
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

        async def patch(self, url, **kwargs):
            return await transport.request("patch", url, **kwargs)

        async def delete(self, url, **kwargs):
            return await transport.request("delete", url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)


def install_calendar_provider(
    monkeypatch,
    transport: FakeHTTP,
    *,
    calendar_id: str = "bot-calendar",
) -> None:
    install_http(monkeypatch, transport)

    async def credentials(_agent_id):
        return "app", "secret"

    async def token(_app_id, _app_secret):
        return "tenant-token"

    async def primary_calendar(_token):
        return calendar_id, None

    async def no_tenant(_agent_id):
        return None

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def no_log(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    monkeypatch.setattr(
        feishu_service,
        "get_tenant_access_token",
        token,
    )
    monkeypatch.setattr(
        agent_tools,
        "_get_agent_calendar_id",
        primary_calendar,
    )
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
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


def install_attendee_directory(monkeypatch, mapping: dict[str, str]) -> None:
    async def search(_agent_id, arguments):
        name = arguments["name"]
        open_id = mapping.get(name)
        if open_id is None:
            return f"No directory match for {name}"
        return f"open_id: `{open_id}`"

    monkeypatch.setattr(agent_tools, "_feishu_user_search", search)


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


def event(event_id: str, summary: str) -> dict:
    return {
        "event_id": event_id,
        "summary": summary,
        "start_time": {"timestamp": "1784170800"},
        "end_time": {"timestamp": "1784174400"},
    }


def tool_definition(name: str) -> dict:
    try:
        return builtin_model_definition(name)
    except KeyError:
        # F0 adds the missing Wiki canonical definition. Keeping a placeholder
        # here lets the F1 visibility assertion fail on the typed gate itself.
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": "F1 contract placeholder",
                "parameters": {"type": "object", "properties": {}},
            },
        }


def test_f1_feishu_tools_are_in_native_typed_workset() -> None:
    assert F1_FEISHU_TOOLS <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_f1_feishu_visibility_requires_local_readiness(monkeypatch) -> None:
    tools = [tool_definition(name) for name in sorted(F1_FEISHU_TOOLS)]

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
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *F1_FEISHU_TOOLS}
        ),
    )

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_f1_feishu_visibility_contains_only_ready_assigned_tools(
    monkeypatch,
) -> None:
    assigned_names = {"feishu_calendar_list", "feishu_wiki_list"}
    tools = [tool_definition(name) for name in sorted(assigned_names)]

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
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *F1_FEISHU_TOOLS}
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {item["function"]["name"] for item in resolved} == assigned_names


@pytest.mark.asyncio
async def test_calendar_list_returns_bot_events_and_enforces_max_results(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "get",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "items": [
                        event("event-1", "One"),
                        event("event-2", "Two"),
                        event("event-3", "Three"),
                    ]
                },
            }
        ),
    )
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_calendar_list", {"max_results": 2}),
        "succeeded",
    )

    assert "event-1" in (outcome.summary or "")
    assert "event-2" in (outcome.summary or "")
    assert "event-3" not in (outcome.summary or "")


@pytest.mark.asyncio
async def test_calendar_list_code_zero_empty_items_is_success(monkeypatch) -> None:
    transport = FakeHTTP()
    transport.add("get", FakeResponse({"code": 0, "data": {"items": []}}))
    install_calendar_provider(monkeypatch, transport)

    assert_outcome(
        await execute("feishu_calendar_list", {}),
        "succeeded",
    )


@pytest.mark.asyncio
async def test_calendar_list_bot_failure_is_not_masked_by_freebusy(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 0, "data": {"freebusy_list": []}}),
    )
    transport.add(
        "get",
        FakeResponse({"code": 230001, "msg": "calendar rejected request"}),
    )
    install_calendar_provider(monkeypatch, transport)
    context_token = agent_tools.channel_feishu_sender_open_id.set("ou_sender")
    try:
        outcome = assert_outcome(
            await execute("feishu_calendar_list", {}),
            "failed",
        )
    finally:
        agent_tools.channel_feishu_sender_open_id.reset(context_token)

    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.asyncio
async def test_calendar_list_freebusy_failure_does_not_replace_bot_result(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 230001, "msg": "freebusy rejected request"}),
    )
    transport.add(
        "get",
        FakeResponse({"code": 0, "data": {"items": [event("event-1", "One")]}}),
    )
    install_calendar_provider(monkeypatch, transport)
    context_token = agent_tools.channel_feishu_sender_open_id.set("ou_sender")
    try:
        outcome = assert_outcome(
            await execute("feishu_calendar_list", {}),
            "succeeded",
        )
    finally:
        agent_tools.channel_feishu_sender_open_id.reset(context_token)

    assert "event-1" in (outcome.summary or "")


@pytest.mark.asyncio
async def test_calendar_list_timeout_is_retryable_failure(monkeypatch) -> None:
    transport = FakeHTTP()
    transport.add("get", httpx.ReadTimeout("calendar read timed out"))
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute("feishu_calendar_list", {}),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.asyncio
async def test_calendar_create_requires_event_id_and_attendee_receipts(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 0, "data": {"event": {"event_id": "event-1"}}}),
        FakeResponse({"code": 0, "data": {}}),
    )
    install_calendar_provider(monkeypatch, transport)
    install_attendee_directory(monkeypatch, {"Alice": "ou_Alice"})

    outcome = assert_outcome(
        await execute(
            "feishu_calendar_create",
            {
                "summary": "Review",
                "start_time": "2026-07-16T10:00:00+08:00",
                "end_time": "2026-07-16T11:00:00+08:00",
                "attendee_names": ["Alice"],
            },
        ),
        "succeeded",
    )

    assert outcome.result_ref == "event-1"
    attendee_calls = [call for call in transport.calls if "/attendees" in call[1]]
    assert len(attendee_calls) == 1


@pytest.mark.asyncio
async def test_calendar_create_missing_event_id_is_unknown(monkeypatch) -> None:
    transport = FakeHTTP()
    transport.add("post", FakeResponse({"code": 0, "data": {"event": {}}}))
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(
            "feishu_calendar_create",
            {
                "summary": "Review",
                "start_time": "2026-07-16T10:00:00+08:00",
                "end_time": "2026-07-16T11:00:00+08:00",
            },
        ),
        "unknown",
    )

    assert outcome.error_code


@pytest.mark.asyncio
async def test_calendar_create_partial_attendee_write_is_failed_with_event_receipt(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 0, "data": {"event": {"event_id": "event-1"}}}),
        FakeResponse({"code": 0, "data": {}}),
        FakeResponse({"code": 230001, "msg": "attendee rejected request"}),
    )
    install_calendar_provider(monkeypatch, transport)
    install_attendee_directory(
        monkeypatch,
        {"Alice": "ou_Alice", "Bob": "ou_Bob"},
    )

    outcome = assert_outcome(
        await execute(
            "feishu_calendar_create",
            {
                "summary": "Review",
                "start_time": "2026-07-16T10:00:00+08:00",
                "end_time": "2026-07-16T11:00:00+08:00",
                "attendee_names": ["Alice", "Bob"],
            },
        ),
        "failed",
    )

    assert outcome.result_ref == "event-1"
    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.asyncio
async def test_calendar_create_indeterminate_attendee_write_is_unknown_with_event_receipt(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 0, "data": {"event": {"event_id": "event-1"}}}),
        httpx.ReadTimeout("attendee write timed out"),
    )
    install_calendar_provider(monkeypatch, transport)
    install_attendee_directory(monkeypatch, {"Alice": "ou_Alice"})

    outcome = assert_outcome(
        await execute(
            "feishu_calendar_create",
            {
                "summary": "Review",
                "start_time": "2026-07-16T10:00:00+08:00",
                "end_time": "2026-07-16T11:00:00+08:00",
                "attendee_names": ["Alice"],
            },
        ),
        "unknown",
    )

    assert outcome.result_ref == "event-1"
    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, method, arguments",
    [
        (
            "feishu_calendar_update",
            "patch",
            {"event_id": "event-1", "summary": "Changed"},
        ),
        ("feishu_calendar_delete", "delete", {"event_id": "event-1"}),
    ],
)
async def test_calendar_mutation_uses_event_id_on_bot_primary_calendar(
    monkeypatch,
    tool_name,
    method,
    arguments,
) -> None:
    transport = FakeHTTP()
    transport.add(method, FakeResponse({"code": 0, "data": {}}))
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, arguments),
        "succeeded",
    )

    assert outcome.result_ref == "event-1"
    assert len(transport.calls) == 1
    assert "/calendars/bot-calendar/events/event-1" in transport.calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, method, arguments",
    [
        (
            "feishu_calendar_update",
            "patch",
            {"event_id": "event-1", "summary": "Changed"},
        ),
        ("feishu_calendar_delete", "delete", {"event_id": "event-1"}),
    ],
)
async def test_calendar_mutation_provider_rejection_is_failed(
    monkeypatch,
    tool_name,
    method,
    arguments,
) -> None:
    transport = FakeHTTP()
    transport.add(
        method,
        FakeResponse({"code": 230001, "msg": "calendar rejected request"}),
    )
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, arguments),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, method, arguments",
    [
        (
            "feishu_calendar_update",
            "patch",
            {"event_id": "event-1", "summary": "Changed"},
        ),
        ("feishu_calendar_delete", "delete", {"event_id": "event-1"}),
    ],
)
async def test_calendar_mutation_timeout_is_unknown(
    monkeypatch,
    tool_name,
    method,
    arguments,
) -> None:
    transport = FakeHTTP()
    transport.add(method, httpx.ReadTimeout("calendar write timed out"))
    install_calendar_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute(tool_name, arguments),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code
