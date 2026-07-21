"""D-020 typed outcomes for the three simple Deploy provider writes.

The Vercel deployment lifecycle and image-generation families are deliberately
outside this batch.  Every provider, value-ref store, and readiness probe in
this module is a local fake.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
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


SIMPLE_DEPLOY_TOOL_NAMES = frozenset(
    {
        "vercel_set_env",
        "vercel_manage_domain",
        "neon_create_database",
    }
)

VERCEL_TOKEN = "vercel-token"
NEON_API_KEY = "neon-api-key"


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload=None,
        *,
        text: str = "",
        json_error: BaseException | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {}, default=str)
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


@dataclass(frozen=True)
class ExpectedCall:
    method: str
    url_suffix: str
    result: object


class ScriptedHTTP:
    """Strict ordered fake; any extra/replayed provider call fails."""

    def __init__(self, *script: ExpectedCall) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, dict]] = []
        self.factory_calls = 0

    def factory(self, *args, **kwargs):
        del args, kwargs
        self.factory_calls += 1
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def _dispatch(self, method: str, url: str, kwargs: dict):
        self.calls.append((method, url, kwargs))
        if not self.script:
            raise AssertionError(f"unexpected or replayed provider call: {method} {url}")
        expected = self.script.pop(0)
        assert method == expected.method
        assert url.endswith(expected.url_suffix)
        if isinstance(expected.result, BaseException):
            raise expected.result
        return expected.result

    async def get(self, url: str, **kwargs):
        return self._dispatch("GET", url, kwargs)

    async def post(self, url: str, **kwargs):
        return self._dispatch("POST", url, kwargs)

    async def patch(self, url: str, **kwargs):
        return self._dispatch("PATCH", url, kwargs)

    def count(self, method: str, url_suffix: str) -> int:
        return sum(call_method == method and url.endswith(url_suffix) for call_method, url, _kwargs in self.calls)

    def assert_done(self) -> None:
        assert self.script == []


class NetworkMustNotBeUsed:
    attempts = 0

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        type(self).attempts += 1
        raise AssertionError("Runtime readiness must not ping deploy providers")


def assert_outcome(
    result,
    status: str,
    *,
    error_code: str | None = None,
) -> ToolExecutionOutcome:
    assert isinstance(result, ToolExecutionOutcome)
    assert result.status == status
    if error_code is not None:
        assert result.error_code == error_code
    return result


def outcome_json(outcome: ToolExecutionOutcome) -> str:
    return json.dumps(
        {
            "status": outcome.status,
            "summary": outcome.summary,
            "result_ref": outcome.result_ref,
            "error_code": outcome.error_code,
            "retryable": outcome.retryable,
            "artifact_refs": outcome.artifact_refs,
            "evidence_refs": outcome.evidence_refs,
            "metadata": outcome.metadata,
        },
        default=str,
        sort_keys=True,
    )


def assert_secret_absent(outcome: ToolExecutionOutcome, secret: str) -> None:
    assert secret not in outcome_json(outcome)
    assert secret not in repr(outcome)


async def execute(
    tool_name: str,
    arguments: dict,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
):
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id,
        user_id,
    )


def install_common_runtime_stubs(monkeypatch) -> None:
    async def tenant_for_agent(_agent_id):
        return "tenant-1"

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_for_agent)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)


def install_vercel(
    monkeypatch,
    provider: ScriptedHTTP,
    *,
    resolve_value_ref=None,
) -> None:
    install_common_runtime_stubs(monkeypatch)

    async def token(_agent_id, _tool_name):
        return VERCEL_TOKEN

    async def default_resolve(_agent_id, _value_ref):
        raise AssertionError("inline value unexpectedly entered value-ref resolver")

    monkeypatch.setattr(agent_tools, "_get_vercel_token", token)
    monkeypatch.setattr(
        agent_tools,
        "_resolve_deploy_value_ref",
        resolve_value_ref or default_resolve,
        raising=False,
    )
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)


def install_neon(
    monkeypatch,
    provider: ScriptedHTTP,
    *,
    stored_values: list[tuple[uuid.UUID, str, dict]] | None = None,
    value_ref: str = "deploy-value://tenant-1/neon/project-1",
) -> None:
    install_common_runtime_stubs(monkeypatch)

    async def config(_agent_id, tool_name):
        assert tool_name == "neon_create_database"
        return {"neon_api_key": NEON_API_KEY}

    async def quota(_api_key):
        assert _api_key == NEON_API_KEY
        return False, ""

    async def store(agent_id, value, **kwargs):
        if stored_values is not None:
            stored_values.append((agent_id, value, dict(kwargs)))
        return value_ref

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_check_neon_quota_limit", quota)
    monkeypatch.setattr(
        agent_tools,
        "_store_deploy_value_ref",
        store,
        raising=False,
    )
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)


def one_of_required_sets(schema: dict) -> set[frozenset[str]]:
    branches = schema.get("oneOf") or schema.get("anyOf") or []
    return {frozenset(str(name) for name in branch.get("required", ())) for branch in branches}


def test_simple_deploy_contracts_are_external_exactly_once_writes() -> None:
    for name in SIMPLE_DEPLOY_TOOL_NAMES:
        assert builtin_policy(name) == {
            "effect": "external_write",
            "retry_policy": "never",
            "parallel_safe": False,
        }


def test_vercel_set_env_schema_accepts_exactly_one_value_source_and_nonempty_targets() -> None:
    schema = builtin_model_definition("vercel_set_env")["function"]["parameters"]

    assert {"project_name", "key"} <= set(schema["required"])
    assert "value" not in schema["required"]
    assert one_of_required_sets(schema) == {
        frozenset({"value"}),
        frozenset({"value_ref"}),
    }
    assert schema["properties"]["target"]["minItems"] == 1
    assert schema["properties"]["value_ref"]["type"] == "string"


def test_simple_deploy_tools_have_local_credential_readiness_and_native_visibility() -> None:
    assert SIMPLE_DEPLOY_TOOL_NAMES <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    vercel_readiness = {
        builtin_readiness("vercel_set_env"),
        builtin_readiness("vercel_manage_domain"),
    }
    assert len(vercel_readiness) == 1
    assert None not in vercel_readiness
    assert "local" not in vercel_readiness
    assert builtin_readiness("neon_create_database") not in {None, "local"}


async def resolve_runtime_tools(
    monkeypatch,
    *,
    configs: dict[str, dict],
) -> set[str]:
    tools = [builtin_model_definition(name) for name in sorted(SIMPLE_DEPLOY_TOOL_NAMES)]

    async def assigned(_agent_id):
        return tools

    async def config(_agent_id, tool_name):
        return dict(configs.get(tool_name, {}))

    async def no_dynamic(_agent_id):
        return set()

    NetworkMustNotBeUsed.attempts = 0
    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(httpx, "AsyncClient", NetworkMustNotBeUsed)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert NetworkMustNotBeUsed.attempts == 0
    return {str(tool.get("function", {}).get("name") or "") for tool in resolved}


@pytest.mark.asyncio
async def test_simple_deploy_visibility_uses_only_shared_vercel_and_neon_local_keys(
    monkeypatch,
) -> None:
    resolved = await resolve_runtime_tools(
        monkeypatch,
        configs={
            "vercel_deploy": {"vercel_token": VERCEL_TOKEN},
            "neon_create_database": {"neon_api_key": NEON_API_KEY},
        },
    )

    assert resolved == SIMPLE_DEPLOY_TOOL_NAMES


@pytest.mark.asyncio
async def test_simple_deploy_visibility_hides_missing_local_credentials_without_ping(
    monkeypatch,
) -> None:
    resolved = await resolve_runtime_tools(monkeypatch, configs={})

    assert resolved == set()


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "target": ["production"],
        },
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": "inline",
            "value_ref": "deploy-value://tenant-1/ref",
            "target": ["production"],
        },
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": "inline",
            "target": [],
        },
    ),
    ids=["missing-value-source", "two-value-sources", "empty-targets"],
)
@pytest.mark.asyncio
async def test_vercel_set_env_rejects_invalid_value_or_target_shape_before_dispatch(
    monkeypatch,
    arguments,
) -> None:
    provider = ScriptedHTTP()
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_set_env",
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert_outcome(result, "failed", error_code="invalid_tool_arguments")
    assert provider.calls == []


@pytest.mark.parametrize(
    "resolution_error",
    (
        PermissionError("value_ref scope mismatch"),
        LookupError("value_ref not found"),
    ),
    ids=["scope-mismatch", "missing-ref"],
)
@pytest.mark.asyncio
async def test_vercel_value_ref_resolution_failure_is_known_before_dispatch(
    monkeypatch,
    resolution_error,
) -> None:
    provider = ScriptedHTTP()

    async def reject_ref(_agent_id, _value_ref):
        raise resolution_error

    install_vercel(monkeypatch, provider, resolve_value_ref=reject_ref)

    result = await execute(
        "vercel_set_env",
        {
            "project_name": "app",
            "key": "DATABASE_URL",
            "value_ref": "deploy-value://other-tenant/ref",
            "target": ["production"],
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert_outcome(result, "failed")
    assert provider.calls == []


@pytest.mark.parametrize("use_value_ref", (False, True), ids=["inline", "opaque-ref"])
@pytest.mark.asyncio
async def test_vercel_set_env_post_uses_encrypted_default_and_stable_receipt(
    monkeypatch,
    use_value_ref,
) -> None:
    agent_id = uuid.uuid4()
    secret = "s3cr3t-value-that-must-not-enter-outcome"
    opaque_ref = "deploy-value://tenant-1/ref-1"
    resolved_refs: list[tuple[uuid.UUID, str]] = []
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/v9/projects/app/env",
            FakeResponse(
                201,
                {
                    "id": "env-1",
                    "key": "PUBLIC_API_TOKEN",
                    "type": "encrypted",
                    "target": ["production"],
                },
            ),
        )
    )

    async def resolve_ref(request_agent_id, value_ref):
        resolved_refs.append((request_agent_id, value_ref))
        return secret

    install_vercel(
        monkeypatch,
        provider,
        resolve_value_ref=resolve_ref if use_value_ref else None,
    )
    arguments = {
        "project_name": "app",
        "key": "PUBLIC_API_TOKEN",
        "target": ["production"],
    }
    arguments["value_ref" if use_value_ref else "value"] = opaque_ref if use_value_ref else secret

    result = await execute(
        "vercel_set_env",
        arguments,
        agent_id=agent_id,
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == "env-1"
    assert_secret_absent(outcome, secret)
    assert provider.count("POST", "/v9/projects/app/env") == 1
    post_payload = provider.calls[0][2]["json"]
    assert post_payload == {
        "key": "PUBLIC_API_TOKEN",
        "value": secret,
        "type": "encrypted",
        "target": ["production"],
    }
    assert opaque_ref not in json.dumps(post_payload)
    assert resolved_refs == ([(agent_id, opaque_ref)] if use_value_ref else [])
    provider.assert_done()


@pytest.mark.asyncio
async def test_vercel_structured_conflict_reconciles_once_then_patches_with_receipt(
    monkeypatch,
) -> None:
    secret = "updated-secret-not-for-outcome"
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/v9/projects/app/env",
            FakeResponse(
                409,
                {"error": {"code": "ENV_ALREADY_EXISTS"}},
            ),
        ),
        ExpectedCall(
            "GET",
            "/v9/projects/app/env",
            FakeResponse(
                200,
                {
                    "envs": [
                        {
                            "id": "env-existing",
                            "key": "API_TOKEN",
                            "type": "encrypted",
                            "target": ["production"],
                        }
                    ]
                },
            ),
        ),
        ExpectedCall(
            "PATCH",
            "/v9/projects/app/env/env-existing",
            FakeResponse(
                200,
                {
                    "id": "env-existing",
                    "key": "API_TOKEN",
                    "type": "encrypted",
                    "target": ["production"],
                },
            ),
        ),
    )
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_set_env",
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": secret,
            "target": ["production"],
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == "env-existing"
    assert_secret_absent(outcome, secret)
    assert provider.count("POST", "/v9/projects/app/env") == 1
    assert provider.count("GET", "/v9/projects/app/env") == 1
    assert provider.count("PATCH", "/v9/projects/app/env/env-existing") == 1
    patch_payload = provider.calls[2][2]["json"]
    assert patch_payload == {
        "value": secret,
        "type": "encrypted",
        "target": ["production"],
    }
    provider.assert_done()


@pytest.mark.parametrize(
    "response",
    (
        FakeResponse(400, {"error": {"code": "INVALID_ENV"}}),
        FakeResponse(403, {"error": {"code": "FORBIDDEN"}}),
        FakeResponse(409, {"error": {"code": "SOME_OTHER_CONFLICT"}}),
    ),
    ids=["bad-request", "forbidden", "unrelated-conflict"],
)
@pytest.mark.asyncio
async def test_vercel_set_env_known_4xx_is_failed_without_conflict_guessing(
    monkeypatch,
    response,
) -> None:
    provider = ScriptedHTTP(ExpectedCall("POST", "/v9/projects/app/env", response))
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_set_env",
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": "hidden",
            "target": ["production"],
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "failed")
    assert_secret_absent(outcome, "hidden")
    assert len(provider.calls) == 1
    provider.assert_done()


@pytest.mark.parametrize(
    "provider_result",
    (
        httpx.ReadTimeout("Vercel create response lost"),
        FakeResponse(500, {"error": {"code": "UPSTREAM"}}),
        FakeResponse(201, {}),
        FakeResponse(201, json_error=ValueError("bad JSON")),
    ),
    ids=["timeout", "server-error", "missing-receipt", "bad-json"],
)
@pytest.mark.asyncio
async def test_vercel_set_env_post_uncertainty_is_unknown_and_never_replayed(
    monkeypatch,
    provider_result,
) -> None:
    secret = "post-secret-not-for-outcome"
    provider = ScriptedHTTP(ExpectedCall("POST", "/v9/projects/app/env", provider_result))
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_set_env",
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": secret,
            "target": ["production"],
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "unknown")
    assert outcome.retryable is False
    assert_secret_absent(outcome, secret)
    assert provider.count("POST", "/v9/projects/app/env") == 1
    provider.assert_done()


@pytest.mark.parametrize(
    ("patch_result", "expected_status"),
    (
        (httpx.ReadTimeout("Vercel patch response lost"), "unknown"),
        (FakeResponse(500, {"error": {"code": "UPSTREAM"}}), "unknown"),
        (FakeResponse(200, {}), "unknown"),
        (FakeResponse(200, {"id": "different-env"}), "unknown"),
        (FakeResponse(400, {"error": {"code": "INVALID_ENV"}}), "failed"),
    ),
    ids=[
        "timeout",
        "server-error",
        "missing-receipt",
        "receipt-mismatch",
        "known-4xx",
    ],
)
@pytest.mark.asyncio
async def test_vercel_set_env_patch_uncertainty_preserves_reconciliation_ref(
    monkeypatch,
    patch_result,
    expected_status,
) -> None:
    secret = "patch-secret-not-for-outcome"
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/v9/projects/app/env",
            FakeResponse(409, {"error": {"code": "ENV_ALREADY_EXISTS"}}),
        ),
        ExpectedCall(
            "GET",
            "/v9/projects/app/env",
            FakeResponse(
                200,
                {"envs": [{"id": "env-existing", "key": "API_TOKEN"}]},
            ),
        ),
        ExpectedCall(
            "PATCH",
            "/v9/projects/app/env/env-existing",
            patch_result,
        ),
    )
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_set_env",
        {
            "project_name": "app",
            "key": "API_TOKEN",
            "value": secret,
            "target": ["production"],
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref == "env-existing"
    assert outcome.retryable is False
    assert_secret_absent(outcome, secret)
    assert provider.count("PATCH", "/v9/projects/app/env/env-existing") == 1
    provider.assert_done()


@pytest.mark.asyncio
async def test_vercel_domain_check_requires_valid_availability_and_price_receipts(
    monkeypatch,
) -> None:
    domain = "available.example"
    provider = ScriptedHTTP(
        ExpectedCall(
            "GET",
            f"/v1/registrar/domains/{domain}/availability",
            FakeResponse(200, {"available": True}),
        ),
        ExpectedCall(
            "GET",
            f"/v1/registrar/domains/{domain}/price",
            FakeResponse(200, {"price": 12, "period": 1}),
        ),
    )
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_manage_domain",
        {"action": "check", "domain": domain},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == domain
    assert "available" in (outcome.summary or "").lower()
    assert "12" in (outcome.summary or "")
    assert provider.count("GET", f"/v1/registrar/domains/{domain}/availability") == 1
    assert provider.count("GET", f"/v1/registrar/domains/{domain}/price") == 1
    provider.assert_done()


@pytest.mark.parametrize(
    "price_result",
    (
        FakeResponse(500, {"error": {"code": "UPSTREAM"}}),
        FakeResponse(200, {}),
        FakeResponse(200, json_error=ValueError("bad JSON")),
    ),
    ids=["price-http-failure", "price-missing", "price-bad-json"],
)
@pytest.mark.asyncio
async def test_vercel_domain_partial_check_never_fabricates_no_or_zero_price(
    monkeypatch,
    price_result,
) -> None:
    domain = "partial.example"
    provider = ScriptedHTTP(
        ExpectedCall(
            "GET",
            f"/v1/registrar/domains/{domain}/availability",
            FakeResponse(200, {"available": True}),
        ),
        ExpectedCall(
            "GET",
            f"/v1/registrar/domains/{domain}/price",
            price_result,
        ),
    )
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_manage_domain",
        {"action": "check", "domain": domain},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "failed")
    summary = outcome.summary or ""
    assert "Available for purchase: No" not in summary
    assert "Price: $0" not in summary
    provider.assert_done()


@pytest.mark.parametrize(
    ("provider_result", "expected_status"),
    (
        (
            FakeResponse(
                201,
                {
                    "name": "app.example.com",
                    "projectId": "project-1",
                    "verified": False,
                },
            ),
            "succeeded",
        ),
        (FakeResponse(201, {}), "unknown"),
        (FakeResponse(201, {"name": "other.example.com"}), "unknown"),
        (httpx.ReadTimeout("bind response lost"), "unknown"),
        (FakeResponse(500, {"error": {"code": "UPSTREAM"}}), "unknown"),
        (FakeResponse(400, {"error": {"code": "INVALID_DOMAIN"}}), "failed"),
    ),
    ids=[
        "success",
        "missing-receipt",
        "mismatched-receipt",
        "timeout",
        "server-error",
        "known-4xx",
    ],
)
@pytest.mark.asyncio
async def test_vercel_domain_bind_settles_only_matching_receipt_once(
    monkeypatch,
    provider_result,
    expected_status,
) -> None:
    domain = "app.example.com"
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/v9/projects/app/domains",
            provider_result,
        )
    )
    install_vercel(monkeypatch, provider)

    result = await execute(
        "vercel_manage_domain",
        {
            "action": "bind",
            "domain": domain,
            "project_name": "app",
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, expected_status)
    assert provider.count("POST", "/v9/projects/app/domains") == 1
    if expected_status == "succeeded":
        assert outcome.result_ref == domain
    if expected_status == "unknown":
        assert outcome.retryable is False
    provider.assert_done()


@pytest.mark.asyncio
async def test_neon_multiple_organizations_requires_explicit_selection_before_create(
    monkeypatch,
) -> None:
    provider = ScriptedHTTP(
        ExpectedCall(
            "GET",
            "/api/v2/users/me/organizations",
            FakeResponse(
                200,
                {
                    "organizations": [
                        {"id": "org-1", "name": "One"},
                        {"id": "org-2", "name": "Two"},
                    ]
                },
            ),
        )
    )
    install_neon(monkeypatch, provider)

    result = await execute(
        "neon_create_database",
        {
            "project_name": "analytics",
            "database_name": "warehouse",
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "failed")
    assert outcome.error_code == "neon_org_selection_required"
    assert "org-1" in (outcome.summary or "")
    assert "org-2" in (outcome.summary or "")
    assert provider.count("POST", "/api/v2/projects") == 0
    provider.assert_done()


@pytest.mark.asyncio
async def test_neon_create_uses_database_name_and_returns_project_plus_opaque_value_ref(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    database_name = "warehouse_custom_7391"
    connection_uri = "postgresql://app:provider-secret@db.example.test/warehouse_custom_7391"
    opaque_ref = "deploy-value://tenant-1/neon/project-1"
    stored_values: list[tuple[uuid.UUID, str, dict]] = []
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/api/v2/projects",
            FakeResponse(
                201,
                {
                    "project": {"id": "project-1"},
                    "connection_uri": connection_uri,
                },
            ),
        )
    )
    install_neon(
        monkeypatch,
        provider,
        stored_values=stored_values,
        value_ref=opaque_ref,
    )

    result = await execute(
        "neon_create_database",
        {
            "project_name": "analytics",
            "database_name": database_name,
            "region": "aws-us-east-1",
            "org_id": "org-1",
        },
        agent_id=agent_id,
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == "project-1"
    assert outcome.metadata.get("value_ref") == opaque_ref
    assert opaque_ref in (outcome.result_summary or "")
    assert stored_values
    assert stored_values[0][0] == agent_id
    assert stored_values[0][1] == connection_uri
    assert_secret_absent(outcome, connection_uri)
    post_payload = provider.calls[0][2]["json"]
    assert database_name in json.dumps(post_payload, sort_keys=True)
    assert post_payload["project"]["org_id"] == "org-1"
    assert provider.count("POST", "/api/v2/projects") == 1
    provider.assert_done()


@pytest.mark.parametrize(
    "connection_result",
    (
        FakeResponse(200, {}),
        FakeResponse(404, {"error": {"code": "NOT_READY"}}),
        httpx.ReadTimeout("connection receipt lookup timed out"),
        FakeResponse(200, json_error=ValueError("bad JSON")),
    ),
    ids=["missing-uri", "known-404", "lookup-timeout", "bad-json"],
)
@pytest.mark.asyncio
async def test_neon_confirmed_project_without_connection_is_known_partial_not_recreated(
    monkeypatch,
    connection_result,
) -> None:
    provider = ScriptedHTTP(
        ExpectedCall(
            "POST",
            "/api/v2/projects",
            FakeResponse(201, {"project": {"id": "project-partial"}}),
        ),
        ExpectedCall(
            "GET",
            "/api/v2/projects/project-partial/connection_string",
            connection_result,
        ),
    )
    install_neon(monkeypatch, provider)

    result = await execute(
        "neon_create_database",
        {
            "project_name": "analytics",
            "database_name": "analytics",
            "org_id": "org-1",
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, "failed")
    assert outcome.result_ref == "project-partial"
    assert outcome.retryable is False
    assert "partial" in (outcome.error_code or "")
    assert "postgresql://alex:password@" not in outcome_json(outcome)
    assert "ep-cool-breeze-12345" not in outcome_json(outcome)
    assert provider.count("POST", "/api/v2/projects") == 1
    provider.assert_done()


@pytest.mark.parametrize(
    ("provider_result", "expected_status"),
    (
        (httpx.ReadTimeout("Neon create response lost"), "unknown"),
        (FakeResponse(500, {"error": {"code": "UPSTREAM"}}), "unknown"),
        (FakeResponse(201, {"project": {}}), "unknown"),
        (FakeResponse(201, json_error=ValueError("bad JSON")), "unknown"),
        (FakeResponse(400, {"error": {"code": "INVALID_PROJECT"}}), "failed"),
        (FakeResponse(403, {"error": {"code": "FORBIDDEN"}}), "failed"),
    ),
    ids=[
        "timeout",
        "server-error",
        "missing-project-receipt",
        "bad-json",
        "known-400",
        "known-403",
    ],
)
@pytest.mark.asyncio
async def test_neon_create_post_settles_unknown_or_failed_without_replay(
    monkeypatch,
    provider_result,
    expected_status,
) -> None:
    provider = ScriptedHTTP(ExpectedCall("POST", "/api/v2/projects", provider_result))
    install_neon(monkeypatch, provider)

    result = await execute(
        "neon_create_database",
        {
            "project_name": "analytics",
            "database_name": "analytics",
            "org_id": "org-1",
        },
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    outcome = assert_outcome(result, expected_status)
    assert provider.count("POST", "/api/v2/projects") == 1
    if expected_status == "unknown":
        assert outcome.retryable is False
    assert "postgresql://alex:password@" not in outcome_json(outcome)
    assert "ep-cool-breeze-12345" not in outcome_json(outcome)
    provider.assert_done()
