"""D-020 typed outcomes for non-default search/read providers."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_readiness,
)


TYPED_SEARCH_TOOLS = {
    "web_search",
    "jina_search",
    "jina_read",
    "exa_search",
    "tavily_search",
    "google_search",
    "bing_search",
}


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def install_http_client(
    monkeypatch,
    *,
    response: FakeResponse | None = None,
    error: Exception | None = None,
) -> None:
    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *args, **kwargs):
            del args, kwargs
            if error is not None:
                raise error
            return response

        async def post(self, *args, **kwargs):
            del args, kwargs
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(httpx, "AsyncClient", Client)


def test_search_provider_readiness_matches_real_credential_requirements() -> None:
    assert builtin_readiness("web_search") == "local"
    assert builtin_readiness("jina_search") == "local"
    assert builtin_readiness("jina_read") == "local"
    for name in {"exa_search", "tavily_search", "google_search", "bing_search"}:
        assert builtin_readiness(name) == "configured_credentials"


@pytest.mark.asyncio
async def test_search_resolver_uses_only_local_configuration(monkeypatch) -> None:
    tools = [builtin_model_definition(name) for name in sorted(TYPED_SEARCH_TOOLS)]

    class NetworkMustNotBeUsed:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("Tool resolution must not probe providers")

    async def fake_tools(_agent_id):
        return tools

    async def no_credentials(_agent_id, name):
        if name == "web_search":
            return {"search_engine": "duckduckgo", "api_key": ""}
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", fake_tools)
    monkeypatch.setattr(agent_tools, "_get_tool_config", no_credentials)
    monkeypatch.setattr(httpx, "AsyncClient", NetworkMustNotBeUsed)
    monkeypatch.setattr(
        agent_tools,
        "get_settings",
        lambda: SimpleNamespace(EXA_API_KEY=""),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    assert {tool["function"]["name"] for tool in resolved} == {
        "web_search",
        "jina_search",
        "jina_read",
    }

    async def unready_google(_agent_id, name):
        if name == "web_search":
            return {"search_engine": "google", "api_key": ""}
        return {}

    monkeypatch.setattr(agent_tools, "_get_tool_config", unready_google)
    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    assert {tool["function"]["name"] for tool in resolved} == {
        "jina_search",
        "jina_read",
    }

    async def configured(_agent_id, name):
        if name == "web_search":
            return {"search_engine": "google", "api_key": "key:cx"}
        return {"api_key": "configured"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    assert {tool["function"]["name"] for tool in resolved} == TYPED_SEARCH_TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(TYPED_SEARCH_TOOLS))
async def test_search_tools_return_native_typed_validation_failures(
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
@pytest.mark.parametrize(
    ("tool_name", "payload", "text"),
    [
        (
            "web_search",
            None,
            '<a class="result__a" href="https://example.test">Result</a>'
            '<a class="result__snippet">Snippet</a>',
        ),
        (
            "jina_search",
            {
                "data": [
                    {
                        "title": "Jina result",
                        "url": "https://example.test/jina",
                        "description": "Jina description",
                    }
                ]
            },
            "",
        ),
        ("jina_read", None, "Readable content. " * 20),
        (
            "exa_search",
            {
                "results": [
                    {
                        "title": "Exa result",
                        "url": "https://example.test/exa",
                        "text": "Exa content",
                    }
                ]
            },
            "",
        ),
        (
            "tavily_search",
            {
                "results": [
                    {
                        "title": "Tavily result",
                        "url": "https://example.test/tavily",
                        "content": "Tavily content",
                    }
                ]
            },
            "",
        ),
        (
            "google_search",
            {
                "items": [
                    {
                        "title": "Google result",
                        "link": "https://example.test/google",
                        "snippet": "Google snippet",
                    }
                ]
            },
            "",
        ),
        (
            "bing_search",
            {
                "webPages": {
                    "value": [
                        {
                            "name": "Bing result",
                            "url": "https://example.test/bing",
                            "snippet": "Bing snippet",
                        }
                    ]
                }
            },
            "",
        ),
    ],
)
async def test_search_tools_use_structured_success_facts(
    monkeypatch,
    tool_name: str,
    payload,
    text: str,
) -> None:
    async def config(_agent_id, name):
        configs = {
            "web_search": {"search_engine": "duckduckgo", "api_key": ""},
            "exa_search": {"api_key": "exa-key"},
            "tavily_search": {"api_key": "tavily-key"},
            "google_search": {"api_key": "google-key:cx", "language": "en"},
            "bing_search": {"api_key": "bing-key", "language": "en-US"},
        }
        return configs.get(name, {})

    async def no_jina_key():
        return ""

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_get_jina_api_key", no_jina_key)
    install_http_client(
        monkeypatch,
        response=FakeResponse(payload=payload, text=text),
    )
    arguments = (
        {"url": "https://example.test/page"}
        if tool_name == "jina_read"
        else {"query": "structured fact"}
    )

    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert outcome.status == "succeeded"
    assert outcome.error_code is None


@pytest.mark.asyncio
async def test_google_http_rejection_and_bing_error_payload_are_failed(
    monkeypatch,
) -> None:
    async def config(_agent_id, name):
        if name == "google_search":
            return {"api_key": "google-key:cx", "language": "en"}
        return {"api_key": "bing-key", "language": "en-US"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    install_http_client(
        monkeypatch,
        response=FakeResponse(status_code=403, payload={"error": {"code": 403}}),
    )
    google = await agent_tools._google_search_outcome(
        {"query": "rejected"},
        uuid.uuid4(),
    )
    assert google.status == "failed"
    assert google.error_code == "google_search_http_error"
    assert google.retryable is False

    install_http_client(
        monkeypatch,
        response=FakeResponse(payload={"errors": [{"code": "InvalidKey"}]}),
    )
    bing = await agent_tools._bing_search_outcome(
        {"query": "rejected"},
        uuid.uuid4(),
    )
    assert bing.status == "failed"
    assert bing.error_code == "bing_search_response_invalid"


@pytest.mark.asyncio
async def test_search_timeout_and_transient_http_failure_are_retryable(
    monkeypatch,
) -> None:
    async def config(_agent_id, name):
        return {"api_key": "exa-key"} if name == "exa_search" else {}

    async def no_jina_key():
        return ""

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_get_jina_api_key", no_jina_key)
    install_http_client(
        monkeypatch,
        error=httpx.TimeoutException("timeout"),
    )
    timeout = await agent_tools._jina_search_outcome(
        {"query": "timeout"},
        uuid.uuid4(),
    )
    assert timeout.status == "failed"
    assert timeout.retryable is True

    install_http_client(
        monkeypatch,
        response=FakeResponse(status_code=503, payload={"error": "unavailable"}),
    )
    transient = await agent_tools._exa_search_outcome(
        {"query": "transient"},
        uuid.uuid4(),
    )
    assert transient.status == "failed"
    assert transient.error_code == "exa_search_http_error"
    assert transient.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("google_search", {}),
        ("bing_search", {}),
    ],
)
async def test_provider_payload_without_success_signal_fails_conservatively(
    monkeypatch,
    tool_name: str,
    payload: dict,
) -> None:
    async def config(_agent_id, name):
        if name == "google_search":
            return {"api_key": "google-key:cx", "language": "en"}
        return {"api_key": "bing-key", "language": "en-US"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    install_http_client(monkeypatch, response=FakeResponse(payload=payload))

    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        {"query": "ambiguous"},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert outcome.status == "failed"
    assert outcome.error_code.endswith("_response_invalid")
