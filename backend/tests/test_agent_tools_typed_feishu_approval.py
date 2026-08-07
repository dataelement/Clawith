"""Focused contracts for Feishu approval definition reads and file uploads."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import uuid

import httpx
import pytest

from app.services import activity_logger, agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_policy,
    builtin_readiness,
)
from app.services.feishu_service import feishu_service


DEFINITION_GET = "feishu_approval_definition_get"
FILE_UPLOAD = "feishu_approval_file_upload"


@pytest.fixture(autouse=True)
def isolate_activity_log(monkeypatch) -> None:
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
            raise AssertionError(f"unexpected {method.upper()} request: {url}")
        response = self.responses[method].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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
    monkeypatch.setattr(feishu_service, "get_tenant_access_token", tenant_token)


def assert_outcome(value: object, status: str) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


def schema_for(tool_name: str) -> dict:
    return builtin_model_definition(tool_name)["function"]["parameters"]


async def definition_get(arguments: dict) -> ToolExecutionOutcome:
    return await agent_tools.execute_builtin_tool_outcome(
        DEFINITION_GET,
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


async def file_upload(
    workspace_root: Path,
    arguments: dict,
) -> ToolExecutionOutcome:
    return await agent_tools._feishu_approval_file_upload_outcome(
        uuid.uuid4(),
        workspace_root,
        arguments,
    )


def test_approval_definition_get_schema_selects_one_bounded_section() -> None:
    schema = schema_for(DEFINITION_GET)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["approval_code"]
    assert set(schema["properties"]) == {
        "approval_code",
        "section",
        "offset",
        "limit",
    }
    assert schema["properties"]["section"]["enum"] == [
        "summary",
        "form",
        "nodes",
    ]
    assert schema["properties"]["limit"]["maximum"] == 50
    assert builtin_policy(DEFINITION_GET) == {
        "effect": "read",
        "retry_policy": "safe",
        "parallel_safe": True,
    }
    assert builtin_readiness(DEFINITION_GET) == "feishu_channel"


def test_approval_file_upload_schema_requires_workspace_file_type() -> None:
    schema = schema_for(FILE_UPLOAD)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["file_path", "file_type"]
    assert set(schema["properties"]) == {"file_path", "file_type"}
    assert schema["properties"]["file_type"]["enum"] == [
        "image",
        "attachment",
    ]
    assert builtin_policy(FILE_UPLOAD) == {
        "effect": "external_write",
        "retry_policy": "never",
        "parallel_safe": False,
    }
    assert builtin_readiness(FILE_UPLOAD) == "feishu_channel"


@pytest.mark.asyncio
async def test_legacy_execute_tool_fails_closed_for_approval_create() -> None:
    result = await agent_tools.execute_tool(
        "feishu_approval_create",
        {
            "approval_code": "expense",
            "target_member_id": str(uuid.uuid4()),
            "form_data": "[]",
        },
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert result == (
        "Feishu approval creation is blocked outside Durable Runtime "
        "conversation confirmation."
    )


@pytest.mark.asyncio
async def test_approval_definition_get_returns_requested_form_window(
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
                    "form": (
                        '[{"id":"amount","type":"amount"},'
                        '{"id":"reason","type":"textarea"}]'
                    ),
                    "node_list": [{"id": "start"}],
                },
            }
        ),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await definition_get(
            {
                "approval_code": "expense/custom",
                "section": "form",
                "offset": 1,
                "limit": 1,
            }
        ),
        "succeeded",
    )

    assert '"id":"reason"' in (outcome.summary or "")
    assert '"id":"amount"' not in (outcome.summary or "")
    assert outcome.metadata == {
        "section": "form",
        "offset": 1,
        "returned_count": 1,
        "has_more": False,
        "next_offset": None,
    }
    assert transport.calls[0][1].endswith("/expense%2Fcustom")


@pytest.mark.asyncio
async def test_approval_definition_get_business_rejection_is_nonretryable(
    monkeypatch,
) -> None:
    transport = FakeHTTP()
    transport.add(
        "get",
        FakeResponse({"code": 99991663, "msg": "permission denied"}),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await definition_get({"approval_code": "expense"}),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "feishu_approval_definition_get_rejected"


@pytest.mark.asyncio
async def test_approval_file_upload_returns_provider_file_code_once(
    monkeypatch,
    tmp_path,
) -> None:
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"receipt-bytes")
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 0, "data": {"code": "file-code-1"}}),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await file_upload(
            tmp_path,
            {"file_path": "receipt.pdf", "file_type": "attachment"},
        ),
        "succeeded",
    )

    assert outcome.result_ref == "file-code-1"
    assert outcome.metadata == {
        "file_name": "receipt.pdf",
        "file_type": "attachment",
        "size_bytes": len(b"receipt-bytes"),
    }
    assert len(transport.calls) == 1
    _, url, kwargs = transport.calls[0]
    assert url.endswith("/approval/openapi/v2/file/upload")
    assert kwargs["data"] == {"name": "receipt.pdf", "type": "attachment"}
    assert kwargs["files"]["content"][:2] == (
        "receipt.pdf",
        b"receipt-bytes",
    )


@pytest.mark.asyncio
async def test_approval_file_upload_timeout_is_unknown_without_replay(
    monkeypatch,
    tmp_path,
) -> None:
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"receipt-bytes")
    transport = FakeHTTP()
    transport.add("post", httpx.ReadTimeout("receipt timed out"))
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await file_upload(
            tmp_path,
            {"file_path": "receipt.pdf", "file_type": "attachment"},
        ),
        "unknown",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "feishu_approval_file_upload_outcome_unknown"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_approval_file_upload_business_rejection_is_failed_without_replay(
    monkeypatch,
    tmp_path,
) -> None:
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"receipt-bytes")
    transport = FakeHTTP()
    transport.add(
        "post",
        FakeResponse({"code": 1390001, "msg": "file rejected"}),
    )
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await file_upload(
            tmp_path,
            {"file_path": "receipt.pdf", "file_type": "attachment"},
        ),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code == "feishu_approval_file_upload_rejected"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_approval_file_upload_rejects_workspace_traversal_before_dispatch(
    monkeypatch,
    tmp_path,
) -> None:
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await file_upload(
            tmp_path,
            {"file_path": "../receipt.pdf", "file_type": "attachment"},
        ),
        "failed",
    )

    assert outcome.error_code == "feishu_approval_file_path_rejected"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_approval_file_upload_rejects_oversized_image_before_dispatch(
    monkeypatch,
    tmp_path,
) -> None:
    image = tmp_path / "receipt.png"
    with image.open("wb") as stream:
        stream.truncate(agent_tools.FEISHU_APPROVAL_IMAGE_MAX_BYTES + 1)
    transport = FakeHTTP()
    install_feishu_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await file_upload(
            tmp_path,
            {"file_path": "receipt.png", "file_type": "image"},
        ),
        "failed",
    )

    assert outcome.error_code == "feishu_approval_file_size_rejected"
    assert transport.calls == []
