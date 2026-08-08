"""D-020 F1 typed execution contracts for Feishu Wiki listing."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services import activity_logger
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.feishu_service import feishu_service


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected Wiki GET request: {url}")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def install_wiki_provider(monkeypatch, transport: FakeHTTP) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            return await transport.get(url, **kwargs)

    async def credentials(_agent_id):
        return "app", "secret"

    async def token(_app_id, _app_secret):
        return "tenant-token"

    async def node(_node_token, _tenant_token):
        return {
            "node_token": "root-node",
            "space_id": "space-1",
            "obj_token": "doc-1",
            "has_child": True,
            "title": "Root",
        }

    async def no_tenant(_agent_id):
        return None

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def no_log(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(agent_tools, "_get_feishu_credentials", credentials)
    monkeypatch.setattr(
        feishu_service,
        "get_tenant_access_token",
        token,
    )
    monkeypatch.setattr(agent_tools, "_feishu_wiki_get_node", node)
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


async def execute(arguments: dict):
    return await agent_tools.execute_builtin_tool_outcome(
        "feishu_wiki_list",
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def assert_outcome(value, status: str) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


def page(
    *items,
    has_more: bool = False,
    page_token: str | None = None,
) -> FakeResponse:
    data = {"items": list(items), "has_more": has_more}
    if page_token is not None:
        data["page_token"] = page_token
    return FakeResponse({"code": 0, "data": data})


def node(
    node_token: str,
    title: str,
    *,
    has_child: bool = False,
) -> dict:
    return {
        "title": title,
        "node_token": node_token,
        "obj_token": f"doc-{node_token}",
        "has_child": has_child,
    }


@pytest.mark.asyncio
async def test_wiki_code_zero_empty_page_is_success(monkeypatch) -> None:
    transport = FakeHTTP(page())
    install_wiki_provider(monkeypatch, transport)

    assert_outcome(
        await execute({"node_token": "root-node"}),
        "succeeded",
    )


@pytest.mark.asyncio
async def test_wiki_nonzero_business_code_is_not_reported_as_empty_success(
    monkeypatch,
) -> None:
    transport = FakeHTTP(
        FakeResponse({"code": 131006, "msg": "Wiki rejected request"})
    )
    install_wiki_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute({"node_token": "root-node"}),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code


@pytest.mark.asyncio
async def test_wiki_timeout_is_retryable_failure(monkeypatch) -> None:
    transport = FakeHTTP(httpx.ReadTimeout("Wiki read timed out"))
    install_wiki_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute({"node_token": "root-node"}),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code


@pytest.mark.asyncio
async def test_wiki_follows_provider_page_tokens_without_losing_items(
    monkeypatch,
) -> None:
    transport = FakeHTTP(
        page(
            node("node-a", "Page A"),
            has_more=True,
            page_token="next-1",
        ),
        page(node("node-b", "Page B")),
    )
    install_wiki_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute({"node_token": "root-node"}),
        "succeeded",
    )

    assert "node-a" in (outcome.summary or "")
    assert "node-b" in (outcome.summary or "")
    assert len(transport.calls) == 2
    assert transport.calls[1][1]["params"]["page_token"] == "next-1"


@pytest.mark.asyncio
async def test_wiki_non_recursive_listing_does_not_fetch_children(
    monkeypatch,
) -> None:
    transport = FakeHTTP(page(node("node-a", "Page A", has_child=True)))
    install_wiki_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute({"node_token": "root-node", "recursive": False}),
        "succeeded",
    )

    assert "node-a" in (outcome.summary or "")
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_wiki_recursive_listing_stops_at_fixed_three_level_boundary(
    monkeypatch,
) -> None:
    transport = FakeHTTP(
        page(node("node-a", "Page A", has_child=True)),
        page(node("node-b", "Page B", has_child=True)),
        page(node("node-c", "Page C", has_child=True)),
    )
    install_wiki_provider(monkeypatch, transport)

    outcome = assert_outcome(
        await execute({"node_token": "root-node", "recursive": True}),
        "succeeded",
    )

    assert "node-a" in (outcome.summary or "")
    assert "node-b" in (outcome.summary or "")
    assert "node-c" in (outcome.summary or "")
    assert len(transport.calls) == 3
