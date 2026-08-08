"""D-020 Deploy A0 contracts, local readiness, and upload preflight.

A0 fixed canonical schemas, deterministic local prerequisites, and Vercel
upload preflight before the later typed Provider batches. Every provider in
this module is a local fake.
"""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_readiness,
)


VERCEL_TOOLS = (
    "vercel_deploy",
    "vercel_list_deployments",
    "vercel_get_deploy_logs",
    "vercel_set_env",
    "vercel_manage_domain",
)
IMAGE_TOOLS = (
    "upload_image",
    "generate_image_siliconflow",
    "generate_image_openai",
    "generate_image_google",
    "generate_image_custom",
)
class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload=None,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload or "")

    def json(self):
        return self._payload


class NetworkMustNotBeUsed:
    attempts = 0

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        type(self).attempts += 1
        raise AssertionError("Runtime readiness must not ping deploy providers")


class ForbiddenHTTP:
    def __init__(self) -> None:
        self.attempts = 0

    def factory(self, *args, **kwargs):
        del args, kwargs
        self.attempts += 1
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args, **_kwargs):
        raise AssertionError("invalid workspace source reached Vercel")

    async def post(self, *_args, **_kwargs):
        raise AssertionError("invalid workspace source reached Vercel")

    async def patch(self, *_args, **_kwargs):
        raise AssertionError("invalid workspace source reached Vercel")


class FakeVercelHTTP:
    def __init__(self, *, file_upload_status: int = 200) -> None:
        self.file_upload_status = file_upload_status
        self.calls: list[tuple[str, str, dict]] = []

    def factory(self, *args, **kwargs):
        del args, kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "/v9/projects/" in url:
            return FakeResponse(200, {"id": "project-1", "name": "project"})
        if "/v13/deployments/" in url:
            return FakeResponse(
                200,
                {
                    "id": "deployment-1",
                    "readyState": "READY",
                    "url": "project.example.vercel.app",
                },
            )
        raise AssertionError(f"unexpected Vercel GET: {url}")

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/v2/files"):
            return FakeResponse(
                self.file_upload_status,
                {},
                text="upload rejected"
                if self.file_upload_status >= 400
                else "",
            )
        if url.endswith("/v13/deployments"):
            return FakeResponse(
                201,
                {
                    "id": "deployment-1",
                    "url": "project.example.vercel.app",
                },
            )
        if url.endswith("/v9/projects"):
            return FakeResponse(201, {"id": "project-1", "name": "project"})
        raise AssertionError(f"unexpected Vercel POST: {url}")

    async def patch(self, url: str, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return FakeResponse(200, {})

    @property
    def deployment_posts(self) -> list[tuple[str, str, dict]]:
        return [
            call
            for call in self.calls
            if call[0] == "POST" and call[1].endswith("/v13/deployments")
        ]

    @property
    def protection_patches(self) -> list[tuple[str, str, dict]]:
        return [call for call in self.calls if call[0] == "PATCH"]


class FakeNeonHTTP:
    def __init__(
        self,
        *,
        create_payload: dict,
        connection_payload: dict | None = None,
    ) -> None:
        self.create_payload = create_payload
        self.connection_payload = connection_payload or {}
        self.calls: list[tuple[str, str, dict]] = []

    def factory(self, *args, **kwargs):
        del args, kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/connection_string"):
            return FakeResponse(200, self.connection_payload)
        raise AssertionError(f"unexpected Neon GET: {url}")

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/projects"):
            return FakeResponse(201, self.create_payload)
        # A correct implementation may create/rename the requested database
        # through a follow-up endpoint rather than the project-create payload.
        return FakeResponse(201, {})


def _tool_names(tools: list[dict]) -> set[str]:
    return {
        str(tool.get("function", {}).get("name") or "")
        for tool in tools
    }


async def _resolve_with_local_configs(
    monkeypatch,
    *,
    names: tuple[str, ...],
    configs: dict[str, dict],
) -> set[str]:
    tools = [builtin_model_definition(name) for name in names]

    async def assigned(_agent_id):
        return tools

    async def config(_agent_id, name):
        return dict(configs.get(name, {}))

    async def no_dynamic_mcp(_agent_id):
        return set()

    NetworkMustNotBeUsed.attempts = 0
    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *names,
            }
        ),
    )
    monkeypatch.setattr(httpx, "AsyncClient", NetworkMustNotBeUsed)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert NetworkMustNotBeUsed.attempts == 0
    return _tool_names(resolved)


def _conditional_requirement(
    schema: dict,
    *,
    discriminator: str,
    value: str,
    required: str,
) -> bool:
    """Recognize normal JSON-Schema if/then or oneOf branch forms."""
    for collection in ("allOf", "oneOf", "anyOf"):
        for clause in schema.get(collection, []):
            condition = clause.get("if", clause)
            consequence = clause.get("then", clause)
            property_schema = condition.get("properties", {}).get(
                discriminator,
                {},
            )
            matches = property_schema.get("const") == value or (
                property_schema.get("enum") == [value]
            )
            if matches and required in consequence.get("required", []):
                return True
    return False


def test_vercel_siblings_share_one_nonlocal_readiness_contract() -> None:
    readiness = {builtin_readiness(name) for name in VERCEL_TOOLS}

    assert len(readiness) == 1
    assert None not in readiness
    assert "local" not in readiness


def test_neon_and_image_tools_have_config_checked_readiness_contracts() -> None:
    for name in ("neon_create_database", *IMAGE_TOOLS):
        assert builtin_readiness(name) not in {None, "local"}


@pytest.mark.asyncio
async def test_vercel_siblings_are_ready_from_only_the_deploy_token_without_ping(
    monkeypatch,
) -> None:
    resolved = await _resolve_with_local_configs(
        monkeypatch,
        names=VERCEL_TOOLS,
        configs={"vercel_deploy": {"vercel_token": "vercel-token"}},
    )

    assert resolved == set(VERCEL_TOOLS)


@pytest.mark.asyncio
async def test_vercel_siblings_are_hidden_when_shared_deploy_token_is_missing(
    monkeypatch,
) -> None:
    resolved = await _resolve_with_local_configs(
        monkeypatch,
        names=VERCEL_TOOLS,
        configs={},
    )

    assert resolved == set()


@pytest.mark.asyncio
async def test_vercel_execution_ignores_sibling_token_and_uses_shared_token(
    monkeypatch,
) -> None:
    lookups: list[str] = []

    async def config(_agent_id, tool_name):
        lookups.append(tool_name)
        if tool_name == "vercel_deploy":
            return {"vercel_token": "shared-deploy-token"}
        return {"vercel_token": "stale-sibling-token"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)

    token = await agent_tools._get_vercel_token(
        uuid.uuid4(),
        "vercel_list_deployments",
    )

    assert token == "shared-deploy-token"
    assert lookups == ["vercel_deploy"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configs", "expected"),
    [
        ({"neon_create_database": {"neon_api_key": "neon-key"}}, {"neon_create_database"}),
        ({}, set()),
    ],
    ids=["configured", "missing-key"],
)
async def test_neon_readiness_uses_only_its_local_api_key_without_ping(
    monkeypatch,
    configs: dict[str, dict],
    expected: set[str],
) -> None:
    resolved = await _resolve_with_local_configs(
        monkeypatch,
        names=("neon_create_database",),
        configs=configs,
    )

    assert resolved == expected


def _ready_image_config(name: str) -> dict:
    if name == "upload_image":
        return {"private_key": "imagekit-key"}
    if name == "generate_image_custom":
        return {
            "api_key": "image-key",
            "base_url": "https://images.example.test/v1",
            "model": "image-model",
            "request_body_template_json": "{}",
            "response_image_path": "data.url",
        }
    return {"api_key": "image-key"}


@pytest.mark.asyncio
@pytest.mark.parametrize("ready_name", IMAGE_TOOLS)
async def test_each_image_tool_uses_only_its_own_local_configuration(
    monkeypatch,
    ready_name: str,
) -> None:
    resolved = await _resolve_with_local_configs(
        monkeypatch,
        names=IMAGE_TOOLS,
        configs={ready_name: _ready_image_config(ready_name)},
    )

    assert resolved == {ready_name}


def test_image_tools_have_native_runtime_outcomes() -> None:
    assert set(IMAGE_TOOLS) <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


def test_vercel_deploy_schema_has_upload_and_github_requirements() -> None:
    schema = builtin_model_definition("vercel_deploy")["function"]["parameters"]

    assert schema["properties"]["deploy_method"]["default"] == "upload"
    assert _conditional_requirement(
        schema,
        discriminator="deploy_method",
        value="upload",
        required="source_dir",
    )
    assert _conditional_requirement(
        schema,
        discriminator="deploy_method",
        value="github",
        required="github_repo",
    )


def test_vercel_domain_bind_schema_requires_project_name_conditionally() -> None:
    schema = builtin_model_definition("vercel_manage_domain")["function"][
        "parameters"
    ]

    assert _conditional_requirement(
        schema,
        discriminator="action",
        value="bind",
        required="project_name",
    )


def test_vercel_env_targets_cannot_be_an_empty_list() -> None:
    schema = builtin_model_definition("vercel_set_env")["function"]["parameters"]

    assert schema["properties"]["target"]["minItems"] == 1


async def _install_vercel_dependencies(monkeypatch) -> None:
    async def token(_agent_id, _tool_name):
        return "vercel-token"

    async def quota(_token):
        return "quota unavailable in fake"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(agent_tools, "_get_vercel_token", token)
    monkeypatch.setattr(agent_tools, "_get_vercel_quota_summary", quota)
    monkeypatch.setattr(agent_tools.asyncio, "sleep", no_sleep)


@pytest.mark.asyncio
async def test_vercel_upload_rejects_parent_traversal_before_provider_io(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-root"
    workspace_root.mkdir()
    outside = tmp_path / "outside-project"
    outside.mkdir()
    (outside / "index.html").write_text("outside", encoding="utf-8")
    provider = ForbiddenHTTP()
    await _install_vercel_dependencies(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)

    result = await agent_tools._vercel_deploy(
        uuid.uuid4(),
        workspace_root,
        {
            "project_name": "unsafe-project",
            "source_dir": "../outside-project",
            "deploy_method": "upload",
        },
    )

    assert result.startswith("❌")
    assert provider.attempts == 0


@pytest.mark.asyncio
async def test_vercel_upload_rejects_nested_symlink_escape_before_provider_io(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-root"
    source = workspace_root / "workspace" / "site"
    source.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("outside secret", encoding="utf-8")
    (source / "leak.txt").symlink_to(outside)
    provider = ForbiddenHTTP()
    await _install_vercel_dependencies(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)

    result = await agent_tools._vercel_deploy(
        uuid.uuid4(),
        workspace_root,
        {
            "project_name": "unsafe-project",
            "source_dir": "workspace/site",
            "deploy_method": "upload",
        },
    )

    assert result.startswith("❌")
    assert provider.attempts == 0


@pytest.mark.asyncio
async def test_unreadable_vercel_file_fails_before_deployment_post(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-root"
    source = workspace_root / "workspace" / "site"
    source.mkdir(parents=True)
    unreadable = source / "unreadable.txt"
    unreadable.write_text("cannot read", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == unreadable:
            raise PermissionError("unreadable fixture")
        return original_read_bytes(path)

    provider = FakeVercelHTTP()
    await _install_vercel_dependencies(monkeypatch)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)

    result = await agent_tools._vercel_deploy(
        uuid.uuid4(),
        workspace_root,
        {
            "project_name": "project",
            "source_dir": "workspace/site",
            "deploy_method": "upload",
        },
    )

    assert result.startswith("❌")
    assert provider.deployment_posts == []


@pytest.mark.asyncio
async def test_vercel_file_upload_rejection_stops_before_deployment_post(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-root"
    source = workspace_root / "workspace" / "site"
    source.mkdir(parents=True)
    (source / "index.html").write_text("hello", encoding="utf-8")
    provider = FakeVercelHTTP(file_upload_status=500)
    await _install_vercel_dependencies(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)

    result = await agent_tools._vercel_deploy(
        uuid.uuid4(),
        workspace_root,
        {
            "project_name": "project",
            "source_dir": "workspace/site",
            "deploy_method": "upload",
        },
    )

    assert result.startswith("⚠️")
    assert provider.deployment_posts == []


@pytest.mark.asyncio
async def test_vercel_deploy_never_disables_project_protection_implicitly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-root"
    source = workspace_root / "workspace" / "site"
    source.mkdir(parents=True)
    (source / "index.html").write_text("hello", encoding="utf-8")
    provider = FakeVercelHTTP()
    await _install_vercel_dependencies(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)

    result = await agent_tools._vercel_deploy(
        uuid.uuid4(),
        workspace_root,
        {
            "project_name": "project",
            "source_dir": "workspace/site",
            "deploy_method": "upload",
        },
    )

    assert "Vercel deployment deployment-1" in result
    assert "project.example.vercel.app" in result
    assert len(provider.deployment_posts) == 1
    assert provider.protection_patches == []


async def _install_neon_dependencies(monkeypatch, provider: FakeNeonHTTP) -> None:
    async def config(_agent_id, _tool_name):
        return {"neon_api_key": "neon-key"}

    async def quota(_api_key):
        return False, ""

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_check_neon_quota_limit", quota)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)


@pytest.mark.asyncio
async def test_neon_create_consumes_requested_database_name_in_provider_calls(
    monkeypatch,
) -> None:
    database_name = "warehouse_custom_7391"
    provider = FakeNeonHTTP(
        create_payload={
            "project": {"id": "project-1"},
            "connection_uri": (
                "postgresql://user:provider-secret@db.example.test/providerdb"
            ),
        }
    )
    await _install_neon_dependencies(monkeypatch, provider)

    await agent_tools._neon_create_database(
        uuid.uuid4(),
        {
            "project_name": "deploy-project",
            "database_name": database_name,
            "org_id": "org-1",
        },
    )

    serialized_calls = json.dumps(provider.calls, default=str)
    assert database_name in serialized_calls


@pytest.mark.asyncio
async def test_neon_missing_provider_uri_never_returns_a_fabricated_connection(
    monkeypatch,
) -> None:
    provider = FakeNeonHTTP(
        create_payload={"project": {"id": "project-1"}},
        connection_payload={},
    )
    await _install_neon_dependencies(monkeypatch, provider)

    result = await agent_tools._neon_create_database(
        uuid.uuid4(),
        {
            "project_name": "analytics-project",
            "database_name": "analytics",
            "org_id": "org-1",
        },
    )

    assert "postgresql://alex:password@" not in result
    assert "ep-cool-breeze-12345" not in result
    assert result.startswith(("❌", "⚠️"))
