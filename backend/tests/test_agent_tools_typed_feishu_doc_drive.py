"""D-020 F3 typed execution contracts for Feishu Doc and Drive tools."""

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


F3_DOC_DRIVE_TOOLS = frozenset(
    {
        "feishu_doc_search",
        "feishu_doc_read",
        "feishu_doc_create",
        "feishu_doc_append",
        "feishu_drive_share",
        "feishu_drive_delete",
    }
)

READ_CASES = (
    ("feishu_doc_search", {"query": "roadmap"}),
    ("feishu_doc_read", {"document_token": "doc1"}),
)


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self) -> None:
        self.responses: dict[str, list[object]] = defaultdict(list)
        self.calls: dict[str, list[tuple[str, dict]]] = defaultdict(list)

    def add(self, method: str, *responses: object) -> None:
        self.responses[method].extend(responses)

    async def request(self, method: str, url: str, **kwargs):
        self.calls[method].append((url, kwargs))
        if not self.responses[method]:
            raise AssertionError(f"unexpected or replayed {method.upper()} request: {url}")
        response = self.responses[method].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeDocDriveProvider:
    def __init__(self) -> None:
        self.http = FakeHTTP()
        self.responses: dict[str, list[object]] = defaultdict(list)
        self.calls: dict[str, list[tuple[tuple, dict]]] = defaultdict(list)
        self.wiki_calls: list[tuple[str, str]] = []
        self.wiki_forbidden = False
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

    async def read_feishu_doc(self, *args, **kwargs):
        return self._dispatch("read_feishu_doc", args, kwargs)

    async def create_feishu_doc(self, *args, **kwargs):
        return self._dispatch("create_feishu_doc", args, kwargs)


def install_doc_drive_provider(
    monkeypatch,
    provider: FakeDocDriveProvider,
) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            return await provider.http.request("get", url, **kwargs)

        async def post(self, url, **kwargs):
            return await provider.http.request("post", url, **kwargs)

        async def delete(self, url, **kwargs):
            return await provider.http.request("delete", url, **kwargs)

    async def credentials(_agent_id):
        return "app-id", "app-secret"

    async def tenant_token(_app_id, _app_secret):
        return "tenant-token"

    async def no_tenant(_agent_id):
        return None

    async def wiki_node(token, auth_token):
        provider.wiki_calls.append((token, auth_token))
        if provider.wiki_forbidden:
            raise AssertionError("ordinary Docx tools must not probe Wiki")
        return None

    async def enrich(*args, **kwargs):
        provider.enrichment_calls.append((args, kwargs))
        if provider.enrichment_error is not None:
            raise provider.enrichment_error
        doc_token = args[1] if len(args) > 1 else kwargs.get("doc_token", "doc1")
        return f"https://tenant.feishu.cn/docx/{doc_token}"

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def no_log(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    monkeypatch.setattr(
        feishu_service,
        "get_tenant_access_token",
        tenant_token,
    )
    monkeypatch.setattr(
        feishu_service,
        "read_feishu_doc",
        provider.read_feishu_doc,
    )
    monkeypatch.setattr(
        feishu_service,
        "create_feishu_doc",
        provider.create_feishu_doc,
    )
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(agent_tools, "_feishu_wiki_get_node", wiki_node)
    monkeypatch.setattr(agent_tools, "_get_feishu_tenant_doc_url", enrich)
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


def empty_read_payload(tool_name: str) -> dict:
    if tool_name == "feishu_doc_search":
        return {
            "code": 0,
            "data": {"docs_entities": [], "total": 0, "has_more": False},
        }
    return {"code": 0, "data": {"content": ""}}


def queue_read(
    provider: FakeDocDriveProvider,
    tool_name: str,
    response: object,
    *,
    status_code: int = 200,
) -> None:
    if tool_name == "feishu_doc_search":
        if isinstance(response, BaseException):
            provider.http.add("post", response)
        else:
            provider.http.add(
                "post",
                FakeResponse(response, status_code=status_code),
            )
        return
    provider.add("read_feishu_doc", response)


def append_metadata() -> FakeResponse:
    return FakeResponse(
        {
            "code": 0,
            "data": {"document": {"body": {"block_id": "body1"}}},
        }
    )


def append_receipt(
    *,
    block_id: str = "block-new",
    revision: int = 8,
) -> FakeResponse:
    return FakeResponse(
        {
            "code": 0,
            "data": {
                "children": [{"block_id": block_id}],
                "document_revision_id": revision,
            },
        }
    )


def share_receipt(member_id: str) -> FakeResponse:
    return FakeResponse(
        {
            "code": 0,
            "data": {
                "member": {
                    "member_type": "openid",
                    "member_id": member_id,
                    "perm": "edit",
                }
            },
        }
    )


def test_f3_doc_drive_tools_are_in_native_typed_workset() -> None:
    assert F3_DOC_DRIVE_TOOLS <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.asyncio
async def test_f3_doc_drive_visibility_requires_local_readiness(monkeypatch) -> None:
    tools = [builtin_model_definition(name) for name in sorted(F3_DOC_DRIVE_TOOLS)]

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
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *F3_DOC_DRIVE_TOOLS,
            }
        ),
    )

    assert await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_f3_doc_drive_visibility_contains_only_ready_assigned_tools(
    monkeypatch,
) -> None:
    assigned_names = {
        "feishu_doc_search",
        "feishu_doc_append",
        "feishu_drive_delete",
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
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *F3_DOC_DRIVE_TOOLS,
            }
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {item["function"]["name"] for item in resolved} == assigned_names


@pytest.mark.parametrize(("tool_name", "arguments"), READ_CASES)
@pytest.mark.asyncio
async def test_doc_reads_accept_code_zero_empty_results(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    provider = FakeDocDriveProvider()
    queue_read(provider, tool_name, empty_read_payload(tool_name))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "succeeded")

    if tool_name == "feishu_doc_read":
        assert outcome.result_ref == "doc1"


@pytest.mark.parametrize(("tool_name", "arguments"), READ_CASES)
@pytest.mark.asyncio
async def test_doc_reads_reject_provider_business_errors(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    provider = FakeDocDriveProvider()
    queue_read(
        provider,
        tool_name,
        {"code": 1770001, "msg": "Feishu rejected the read"},
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is False
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(("tool_name", "arguments"), READ_CASES)
@pytest.mark.asyncio
async def test_doc_http_failures_are_typed_retryable_reads(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    provider = FakeDocDriveProvider()
    if tool_name == "feishu_doc_search":
        queue_read(
            provider,
            tool_name,
            {"code": 0, "data": {"docs_entities": []}},
            status_code=503,
        )
    else:
        queue_read(
            provider,
            tool_name,
            FeishuAPIError(
                stage="doc_read",
                http_status=503,
                msg="Feishu temporarily unavailable",
            ),
        )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is True
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(("tool_name", "arguments"), READ_CASES)
@pytest.mark.asyncio
async def test_doc_read_timeouts_are_retryable_failures(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    provider = FakeDocDriveProvider()
    queue_read(provider, tool_name, httpx.ReadTimeout("Doc read timed out"))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.retryable is True
    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "payload"),
    (
        (
            "feishu_doc_search",
            {"query": "roadmap"},
            {"code": 0, "data": {"docs_entities": "not-a-list"}},
        ),
        (
            "feishu_doc_read",
            {"document_token": "doc1"},
            {"code": 0, "data": {"content": ["not", "text"]}},
        ),
    ),
)
@pytest.mark.asyncio
async def test_doc_reads_fail_closed_on_malformed_success_payloads(
    monkeypatch,
    tool_name,
    arguments,
    payload,
) -> None:
    provider = FakeDocDriveProvider()
    queue_read(provider, tool_name, payload)
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(await execute(tool_name, arguments), "failed")

    assert outcome.error_code
    assert outcome.error_code != "untyped_tool_outcome"


@pytest.mark.asyncio
async def test_doc_search_clamps_count_and_offset_before_dispatch(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {"docs_entities": [], "total": 0, "has_more": False},
            }
        ),
    )
    install_doc_drive_provider(monkeypatch, provider)

    assert_outcome(
        await execute(
            "feishu_doc_search",
            {"query": "roadmap", "count": 500, "offset": -20},
        ),
        "succeeded",
    )

    payload = provider.http.calls["post"][0][1]["json"]
    assert payload["count"] == 50
    assert payload["offset"] == 0


@pytest.mark.asyncio
async def test_doc_search_requires_a_stable_docs_token_per_result(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "docs_entities": [
                        {"title": "Roadmap", "docs_type": "docx"}
                    ],
                    "total": 1,
                    "has_more": False,
                },
            }
        ),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_search", {"query": "roadmap"}),
        "failed",
    )

    assert outcome.error_code


@pytest.mark.asyncio
async def test_doc_search_exposes_provider_document_tokens(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        FakeResponse(
            {
                "code": 0,
                "data": {
                    "docs_entities": [
                        {
                            "title": "Roadmap",
                            "docs_type": "docx",
                            "docs_token": "doc-roadmap",
                            "owner_id": "ou_owner",
                        }
                    ],
                    "total": 1,
                    "has_more": False,
                },
            }
        ),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_search", {"query": "roadmap"}),
        "succeeded",
    )

    assert "doc-roadmap" in (outcome.summary or "")


@pytest.mark.asyncio
async def test_doc_read_requires_explicit_document_token_without_url_guess(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.wiki_forbidden = True
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_read",
            {"url": "https://tenant.feishu.cn/wiki/wiki1"},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert provider.call_count("read_feishu_doc") == 0
    assert provider.wiki_calls == []


@pytest.mark.asyncio
async def test_doc_read_uses_explicit_docx_token_without_wiki_probe(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.wiki_forbidden = True
    provider.add(
        "read_feishu_doc",
        {"code": 0, "data": {"content": "Document body"}},
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_read", {"document_token": "doc1"}),
        "succeeded",
    )

    assert outcome.result_ref == "doc1"
    assert provider.wiki_calls == []


@pytest.mark.asyncio
async def test_doc_read_enforces_twenty_thousand_character_bound(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.add(
        "read_feishu_doc",
        {"code": 0, "data": {"content": "A" * 20000 + "TAIL-SENTINEL"}},
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_read",
            {"document_token": "doc1", "max_chars": 50000},
        ),
        "succeeded",
    )

    assert "TAIL-SENTINEL" not in (outcome.summary or "")


@pytest.mark.asyncio
async def test_doc_create_is_ordinary_docx_with_stable_document_receipt(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.wiki_forbidden = True
    provider.enrichment_error = RuntimeError("tenant domain lookup unavailable")
    provider.add(
        "create_feishu_doc",
        {
            "code": 0,
            "data": {
                "document": {"document_id": "doc-new", "title": "Roadmap"}
            },
        },
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_create",
            {"title": "Roadmap", "folder_token": "folder1"},
        ),
        "succeeded",
    )

    assert outcome.result_ref == "doc-new"
    assert provider.call_count("create_feishu_doc") == 1
    assert provider.wiki_calls == []
    args, kwargs = provider.calls["create_feishu_doc"][0]
    assert "folder1" in args or "folder1" in kwargs.values()
    assert "Roadmap" in args or "Roadmap" in kwargs.values()


@pytest.mark.asyncio
async def test_doc_create_code_zero_without_document_token_is_unknown(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.add(
        "create_feishu_doc",
        {"code": 0, "data": {"document": {"title": "Roadmap"}}},
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_create", {"title": "Roadmap"}),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert provider.call_count("create_feishu_doc") == 1


@pytest.mark.asyncio
async def test_doc_create_business_rejection_is_failed_without_replay(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.add(
        "create_feishu_doc",
        {"code": 1770001, "msg": "Create rejected"},
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_create", {"title": "Roadmap"}),
        "failed",
    )

    assert outcome.retryable is False
    assert provider.call_count("create_feishu_doc") == 1


@pytest.mark.asyncio
async def test_doc_create_dispatch_timeout_is_unknown_and_never_replayed(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.add(
        "create_feishu_doc",
        httpx.ReadTimeout("create receipt timed out"),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute("feishu_doc_create", {"title": "Roadmap"}),
        "unknown",
    )

    assert outcome.retryable is False
    assert provider.call_count("create_feishu_doc") == 1


@pytest.mark.asyncio
async def test_doc_append_returns_stable_block_and_revision_receipt(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.enrichment_error = RuntimeError("tenant domain lookup unavailable")
    provider.http.add("get", append_metadata())
    provider.http.add("post", append_receipt())
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_append",
            {"document_token": "doc1", "content": "New paragraph"},
        ),
        "succeeded",
    )

    assert outcome.result_ref == "block-new"
    assert "block-new" in (outcome.summary or "")
    assert "8" in (outcome.summary or "")
    assert len(provider.http.calls["post"]) == 1


@pytest.mark.asyncio
async def test_doc_append_code_zero_without_block_revision_is_unknown(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add("get", append_metadata())
    provider.http.add("post", FakeResponse({"code": 0, "data": {}}))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_append",
            {"document_token": "doc1", "content": "New paragraph"},
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["post"]) == 1


@pytest.mark.asyncio
async def test_doc_append_business_rejection_is_failed_without_replay(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add("get", append_metadata())
    provider.http.add(
        "post",
        FakeResponse({"code": 1770001, "msg": "Append rejected"}),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_append",
            {"document_token": "doc1", "content": "New paragraph"},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["post"]) == 1


@pytest.mark.asyncio
async def test_doc_append_dispatch_timeout_is_unknown_and_never_replayed(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add("get", append_metadata())
    provider.http.add("post", httpx.ReadTimeout("append receipt timed out"))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_doc_append",
            {"document_token": "doc1", "content": "New paragraph"},
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["post"]) == 1


@pytest.mark.parametrize(
    ("action", "http_method"),
    (("add", "post"), ("remove", "delete")),
)
@pytest.mark.asyncio
async def test_drive_share_records_one_receipt_per_member(
    monkeypatch,
    action,
    http_method,
) -> None:
    provider = FakeDocDriveProvider()
    provider.enrichment_error = RuntimeError("tenant domain lookup unavailable")
    responses = (
        share_receipt("ou_member1") if action == "add" else FakeResponse({"code": 0}),
        share_receipt("ou_member2") if action == "add" else FakeResponse({"code": 0}),
    )
    provider.http.add(http_method, *responses)
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_share",
            {
                "document_token": "doc1",
                "doc_type": "docx",
                "action": action,
                "member_open_ids": ["ou_member1", "ou_member2"],
                "permission": "edit",
            },
        ),
        "succeeded",
    )

    assert outcome.result_ref == "doc1"
    assert "ou_member1" in (outcome.summary or "")
    assert "ou_member2" in (outcome.summary or "")
    assert len(provider.http.calls[http_method]) == 2


@pytest.mark.asyncio
async def test_drive_share_code_zero_without_member_receipt_is_unknown(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        FakeResponse({"code": 0, "data": {"member": {}}}),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_share",
            {
                "document_token": "doc1",
                "action": "add",
                "member_open_ids": ["ou_member1"],
            },
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["post"]) == 1


@pytest.mark.asyncio
async def test_drive_share_known_partial_result_is_failed_with_member_receipts(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        share_receipt("ou_member1"),
        FakeResponse({"code": 99991672, "msg": "Permission rejected"}),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_share",
            {
                "document_token": "doc1",
                "action": "add",
                "member_open_ids": ["ou_member1", "ou_member2"],
            },
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.result_ref == "doc1"
    assert "ou_member1" in (outcome.summary or "")
    assert "ou_member2" in (outcome.summary or "")
    assert len(provider.http.calls["post"]) == 2


@pytest.mark.asyncio
async def test_drive_share_dispatch_unknown_stops_without_replay_or_next_member(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "post",
        share_receipt("ou_member1"),
        httpx.ReadTimeout("member receipt timed out"),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_share",
            {
                "document_token": "doc1",
                "action": "add",
                "member_open_ids": [
                    "ou_member1",
                    "ou_member2",
                    "ou_member3",
                ],
            },
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.result_ref == "doc1"
    assert "ou_member1" in (outcome.summary or "")
    assert "ou_member2" in (outcome.summary or "")
    assert len(provider.http.calls["post"]) == 2


@pytest.mark.asyncio
async def test_drive_delete_code_zero_uses_file_token_receipt(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.enrichment_error = RuntimeError("tenant domain lookup unavailable")
    provider.http.add("delete", FakeResponse({"code": 0, "data": {}}))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_delete",
            {"file_token": "doc-delete", "file_type": "docx"},
        ),
        "succeeded",
    )

    assert outcome.result_ref == "doc-delete"
    assert len(provider.http.calls["delete"]) == 1


@pytest.mark.asyncio
async def test_drive_folder_delete_uses_provider_task_receipt(monkeypatch) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "delete",
        FakeResponse({"code": 0, "data": {"task_id": "task-delete-1"}}),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_delete",
            {"file_token": "folder-delete", "file_type": "folder"},
        ),
        "succeeded",
    )

    assert outcome.result_ref == "task-delete-1"
    assert "folder-delete" in (outcome.summary or "")
    assert len(provider.http.calls["delete"]) == 1


@pytest.mark.asyncio
async def test_drive_folder_delete_without_task_receipt_is_unknown(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add("delete", FakeResponse({"code": 0, "data": {}}))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_delete",
            {"file_token": "folder-delete", "file_type": "folder"},
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["delete"]) == 1


@pytest.mark.asyncio
async def test_drive_delete_business_rejection_is_failed_without_replay(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add(
        "delete",
        FakeResponse({"code": 1061004, "msg": "Delete rejected"}),
    )
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_delete",
            {"file_token": "doc-delete", "file_type": "docx"},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["delete"]) == 1


@pytest.mark.asyncio
async def test_drive_delete_dispatch_timeout_is_unknown_and_never_replayed(
    monkeypatch,
) -> None:
    provider = FakeDocDriveProvider()
    provider.http.add("delete", httpx.ReadTimeout("delete receipt timed out"))
    install_doc_drive_provider(monkeypatch, provider)

    outcome = assert_outcome(
        await execute(
            "feishu_drive_delete",
            {"file_token": "doc-delete", "file_type": "docx"},
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert len(provider.http.calls["delete"]) == 1
