"""Typed Vercel read contracts using only local provider fakes."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.services import activity_logger, agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import builtin_model_definition


VERCEL_READ_TOOLS = (
    "vercel_list_deployments",
    "vercel_get_deploy_logs",
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | BaseException,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeVercelHTTP:
    def __init__(
        self,
        *,
        response: FakeResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def factory(self, *args, **kwargs):
        del args, kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("Vercel fake has no response")
        return self.response


class ProviderCallForbidden:
    attempts = 0

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        type(self).attempts += 1
        raise AssertionError("Runtime readiness must not ping Vercel")


def _default_arguments(tool_name: str) -> dict:
    if tool_name == "vercel_list_deployments":
        return {"project_name": "clawith-web"}
    return {"deployment_id": "dpl_abc123"}


def _valid_payload(tool_name: str, *, empty: bool) -> object:
    if tool_name == "vercel_list_deployments":
        return {
            "deployments": []
            if empty
            else [
                {
                    "uid": "dpl_abc123",
                    "url": "clawith-web-abc.vercel.app",
                    "state": "READY",
                    "created": 1_752_620_400_000,
                }
            ]
        }
    return (
        []
        if empty
        else [
            {
                "type": "stdout",
                "payload": {"text": "Build completed successfully"},
            }
        ]
    )


def _malformed_payload(tool_name: str) -> object:
    if tool_name == "vercel_list_deployments":
        return {"deployments": "not-a-list"}
    return {"events": "not-a-list"}


def _assert_typed(
    value: ToolExecutionOutcome | str,
    expected_status: str,
) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == expected_status
    return value


def _install_execution_fakes(
    monkeypatch,
    fake_http: FakeVercelHTTP,
) -> None:
    async def shared_token(_agent_id, tool_name):
        assert tool_name in VERCEL_READ_TOOLS
        return "vercel-shared-token"

    async def no_tenant(_agent_id):
        return None

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "_get_vercel_token", shared_token)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)
    monkeypatch.setattr(httpx, "AsyncClient", fake_http.factory)


async def _execute(
    tool_name: str,
    arguments: dict,
) -> ToolExecutionOutcome | str:
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def test_vercel_read_parameter_schemas_require_nonempty_values() -> None:
    list_schema = builtin_model_definition("vercel_list_deployments")[
        "function"
    ]["parameters"]
    logs_schema = builtin_model_definition("vercel_get_deploy_logs")[
        "function"
    ]["parameters"]

    assert list_schema["required"] == ["project_name"]
    assert list_schema["properties"]["project_name"]["minLength"] == 1
    assert logs_schema["required"] == ["deployment_id"]
    assert logs_schema["properties"]["deployment_id"]["minLength"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
async def test_vercel_read_is_visible_only_when_assigned_and_shared_token_is_ready(
    monkeypatch,
    tool_name: str,
) -> None:
    config_lookups: list[str] = []

    async def assigned(_agent_id):
        return [builtin_model_definition(tool_name)]

    async def no_dynamic_mcp(_agent_id):
        return set()

    async def config(_agent_id, requested_name):
        config_lookups.append(requested_name)
        if requested_name == "vercel_deploy":
            return {"vercel_token": "vercel-shared-token"}
        return {}

    ProviderCallForbidden.attempts = 0
    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(httpx, "AsyncClient", ProviderCallForbidden)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert tool_name in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert [tool["function"]["name"] for tool in resolved] == [tool_name]
    assert "vercel_deploy" in config_lookups
    assert ProviderCallForbidden.attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
async def test_vercel_read_is_hidden_when_shared_token_is_missing(
    monkeypatch,
    tool_name: str,
) -> None:
    async def assigned(_agent_id):
        return [builtin_model_definition(tool_name)]

    async def no_dynamic_mcp(_agent_id):
        return set()

    async def no_config(_agent_id, _requested_name):
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", no_config)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert resolved == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("vercel_list_deployments", {}),
        ("vercel_list_deployments", {"project_name": ""}),
        ("vercel_list_deployments", {"project_name": "   "}),
        ("vercel_get_deploy_logs", {}),
        ("vercel_get_deploy_logs", {"deployment_id": ""}),
        ("vercel_get_deploy_logs", {"deployment_id": "   "}),
        ("vercel_get_deploy_logs", {"deployment_id": "https://"}),
        (
            "vercel_get_deploy_logs",
            {"deployment_id": "ftp://clawith-web.vercel.app"},
        ),
    ],
)
async def test_vercel_reads_reject_invalid_parameters_before_http(
    monkeypatch,
    tool_name: str,
    arguments: dict,
) -> None:
    fake = FakeVercelHTTP(
        error=AssertionError("invalid parameters reached Vercel")
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(await _execute(tool_name, arguments), "failed")

    assert outcome.error_code == "invalid_tool_arguments"
    assert outcome.retryable is False
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
async def test_vercel_read_accepts_an_explicit_empty_provider_collection(
    monkeypatch,
    tool_name: str,
) -> None:
    fake = FakeVercelHTTP(
        response=FakeResponse(200, _valid_payload(tool_name, empty=True))
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "succeeded",
    )

    assert "no " in (outcome.summary or "").lower()
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
async def test_vercel_read_accepts_valid_provider_data(
    monkeypatch,
    tool_name: str,
) -> None:
    fake = FakeVercelHTTP(
        response=FakeResponse(200, _valid_payload(tool_name, empty=False))
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "succeeded",
    )

    expected_text = (
        "dpl_abc123"
        if tool_name == "vercel_list_deployments"
        else "Build completed successfully"
    )
    assert expected_text in (outcome.summary or "")
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_vercel_read_known_client_rejection_is_nonretryable(
    monkeypatch,
    tool_name: str,
    status_code: int,
) -> None:
    fake = FakeVercelHTTP(
        response=FakeResponse(
            status_code,
            {"error": {"code": "request_rejected"}},
            text="request rejected",
        )
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is False
    assert outcome.error_code
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_vercel_read_transient_http_status_is_retryable(
    monkeypatch,
    tool_name: str,
    status_code: int,
) -> None:
    fake = FakeVercelHTTP(
        response=FakeResponse(
            status_code,
            {"error": {"code": "temporarily_unavailable"}},
            text="temporarily unavailable",
        )
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
@pytest.mark.parametrize(
    "provider_error",
    [
        httpx.TimeoutException("Vercel timed out"),
        httpx.ReadError(
            "connection reset",
            request=httpx.Request("GET", "https://api.vercel.com"),
        ),
    ],
    ids=["timeout", "reset"],
)
async def test_vercel_read_transport_failure_is_retryable(
    monkeypatch,
    tool_name: str,
    provider_error: BaseException,
) -> None:
    fake = FakeVercelHTTP(error=provider_error)
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", VERCEL_READ_TOOLS)
@pytest.mark.parametrize("malformed_kind", ["bad-json", "bad-shape"])
async def test_vercel_read_malformed_success_response_is_retryable(
    monkeypatch,
    tool_name: str,
    malformed_kind: str,
) -> None:
    payload: object | BaseException = (
        ValueError("invalid JSON")
        if malformed_kind == "bad-json"
        else _malformed_payload(tool_name)
    )
    fake = FakeVercelHTTP(response=FakeResponse(200, payload))
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(tool_name, _default_arguments(tool_name)),
        "failed",
    )

    assert outcome.retryable is True
    assert outcome.error_code
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_deploy_log_error_payload_is_not_reported_as_no_logs(
    monkeypatch,
) -> None:
    fake = FakeVercelHTTP(
        response=FakeResponse(
            200,
            {
                "error": {
                    "code": "deployment_not_found",
                    "message": "Deployment does not exist",
                }
            },
        )
    )
    _install_execution_fakes(monkeypatch, fake)

    outcome = _assert_typed(
        await _execute(
            "vercel_get_deploy_logs",
            {"deployment_id": "dpl_missing"},
        ),
        "failed",
    )

    assert outcome.retryable is True
    assert "no logs" not in (outcome.summary or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deployment_reference", "expected_segment"),
    [
        ("dpl_abc123", "dpl_abc123"),
        (
            "https://clawith-web-abc.vercel.app/build/details?source=chat",
            "clawith-web-abc.vercel.app",
        ),
    ],
    ids=["deployment-id", "deployment-url"],
)
async def test_deploy_logs_resolves_explicit_id_or_https_url(
    monkeypatch,
    deployment_reference: str,
    expected_segment: str,
) -> None:
    fake = FakeVercelHTTP(response=FakeResponse(200, []))
    _install_execution_fakes(monkeypatch, fake)

    _assert_typed(
        await _execute(
            "vercel_get_deploy_logs",
            {"deployment_id": deployment_reference},
        ),
        "succeeded",
    )

    assert len(fake.calls) == 1
    requested_url = fake.calls[0][0]
    assert requested_url.endswith(f"/{expected_segment}/events")
    assert deployment_reference not in requested_url or deployment_reference == expected_segment
