"""D-020 multi-stage typed outcome contracts for ``vercel_deploy``.

All provider calls are local fakes.  Upload-mode tests require a complete local
manifest before provider I/O; GitHub mode deploys an existing repository/ref
and deliberately has no workspace-push semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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


VERCEL_TOKEN = "vercel-token"
PROJECT_NAME = "app"
PROJECT_ID = "project-1"
DEPLOYMENT_ID = "deployment-1"
DEPLOYMENT_HOST = "app-abc.vercel.app"
DEPLOYMENT_URL = f"https://{DEPLOYMENT_HOST}"


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


class ScriptedVercel:
    """Strict ordered provider fake; unexpected or replayed calls fail."""

    def __init__(self, *script: ExpectedCall, before_call=None) -> None:
        self.script = list(script)
        self.before_call = before_call
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
        if self.before_call is not None:
            self.before_call(method, url, kwargs)
        self.calls.append((method, url, kwargs))
        if not self.script:
            raise AssertionError(f"unexpected or replayed Vercel call: {method} {url}")
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

    def count(self, method: str, url_suffix: str | None = None) -> int:
        return sum(
            call_method == method and (url_suffix is None or url.endswith(url_suffix))
            for call_method, url, _kwargs in self.calls
        )

    def matching(self, method: str, url_suffix: str) -> list[tuple[str, str, dict]]:
        return [call for call in self.calls if call[0] == method and call[1].endswith(url_suffix)]

    def assert_done(self) -> None:
        assert self.script == []


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


def conditional_requirement(
    schema: dict,
    *,
    discriminator: str,
    value: str,
    required: str,
) -> bool:
    for collection in ("allOf", "oneOf", "anyOf"):
        for clause in schema.get(collection, []):
            condition = clause.get("if", clause)
            consequence = clause.get("then", clause)
            property_schema = condition.get("properties", {}).get(
                discriminator,
                {},
            )
            matches = property_schema.get("const") == value or (property_schema.get("enum") == [value])
            if matches and required in consequence.get("required", []):
                return True
    return False


def create_workspace(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path]:
    workspace_root = tmp_path / "agent-root"
    source = workspace_root / "workspace" / "site"
    source.mkdir(parents=True)
    for rel_path, content in files.items():
        target = source / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return workspace_root, source


def sha1(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def project_receipt(
    *,
    project_id: str = PROJECT_ID,
    link: dict | None = None,
) -> dict:
    payload = {"id": project_id, "name": PROJECT_NAME}
    if link is not None:
        payload["link"] = link
    return payload


def deployment_receipt(
    state: str = "QUEUED",
    *,
    deployment_id: str = DEPLOYMENT_ID,
    host: str = DEPLOYMENT_HOST,
) -> dict:
    return {
        "id": deployment_id,
        "url": host,
        "readyState": state,
    }


def upload_success_script(
    digests: tuple[str, ...],
    *,
    project_response: FakeResponse | None = None,
    deployment_state: str = "QUEUED",
    final_state: str = "READY",
) -> list[ExpectedCall]:
    script = [
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            project_response or FakeResponse(200, project_receipt()),
        )
    ]
    script.extend(ExpectedCall("POST", "/v2/files", FakeResponse(200, {})) for _digest in digests)
    script.extend(
        [
            ExpectedCall(
                "POST",
                "/v13/deployments",
                FakeResponse(201, deployment_receipt(deployment_state)),
            ),
            ExpectedCall(
                "GET",
                f"/v13/deployments/{DEPLOYMENT_ID}",
                FakeResponse(200, deployment_receipt(final_state)),
            ),
        ]
    )
    return script


def install_vercel(
    monkeypatch,
    provider: ScriptedVercel,
    *,
    workspace_root: Path,
) -> None:
    async def token(_agent_id, tool_name):
        assert tool_name == "vercel_deploy"
        return VERCEL_TOKEN

    async def quota(_token):
        assert _token == VERCEL_TOKEN
        return "quota omitted by fake"

    async def no_sleep(_seconds):
        return None

    async def tenant_for_agent(_agent_id):
        return "tenant-1"

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "_get_vercel_token", token)
    monkeypatch.setattr(agent_tools, "_get_vercel_quota_summary", quota)
    monkeypatch.setattr(agent_tools.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_for_agent)
    monkeypatch.setattr(
        agent_tools,
        "_agent_workspace_root",
        lambda _agent_id: workspace_root,
    )
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)
    monkeypatch.setattr(httpx, "AsyncClient", provider.factory)


async def execute(
    arguments: dict,
    *,
    agent_id: uuid.UUID | None = None,
) -> ToolExecutionOutcome | str:
    return await agent_tools.execute_builtin_tool_outcome(
        "vercel_deploy",
        arguments,
        agent_id or uuid.uuid4(),
        uuid.uuid4(),
    )


def assert_deployment_posted_once(provider: ScriptedVercel) -> None:
    assert provider.count("POST", "/v13/deployments") == 1


def assert_no_deployment_post(provider: ScriptedVercel) -> None:
    assert provider.count("POST", "/v13/deployments") == 0


def assert_no_implicit_project_patch(provider: ScriptedVercel) -> None:
    assert provider.count("PATCH") == 0


def assert_confirmed_digests(
    outcome: ToolExecutionOutcome,
    expected: list[str] | tuple[str, ...],
) -> None:
    assert outcome.metadata.get("confirmed_blob_digests") == list(expected)


def test_vercel_deploy_contract_is_typed_external_exactly_once() -> None:
    assert "vercel_deploy" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert builtin_policy("vercel_deploy") == {
        "effect": "external_write",
        "retry_policy": "never",
        "parallel_safe": False,
    }
    assert builtin_readiness("vercel_deploy") not in {None, "local"}


def test_vercel_deploy_schema_separates_upload_from_existing_github_repo() -> None:
    definition = builtin_model_definition("vercel_deploy")["function"]
    schema = definition["parameters"]
    description = " ".join(
        [
            str(definition.get("description") or ""),
            str(schema["properties"]["deploy_method"].get("description") or ""),
            str(schema["properties"]["github_repo"].get("description") or ""),
            str(schema["properties"]["git_ref"].get("description") or ""),
        ]
    ).lower()

    assert "project_name" in schema["required"]
    assert "source_dir" not in schema["required"]
    assert conditional_requirement(
        schema,
        discriminator="deploy_method",
        value="upload",
        required="source_dir",
    )
    assert conditional_requirement(
        schema,
        discriminator="deploy_method",
        value="github",
        required="github_repo",
    )
    assert schema["properties"]["git_ref"]["default"] == "main"
    assert "push" not in description
    assert "existing" in description


@pytest.mark.asyncio
async def test_upload_builds_complete_manifest_before_first_provider_call(
    monkeypatch,
    tmp_path,
) -> None:
    files = {
        "b.txt": b"second file",
        "nested/a.txt": b"first file",
    }
    workspace_root, source = create_workspace(tmp_path, files)
    expected_digests = {sha1(content) for content in files.values()}
    read_paths: set[Path] = set()
    original_read_bytes = Path.read_bytes

    def tracked_read_bytes(path: Path) -> bytes:
        content = original_read_bytes(path)
        if source in path.parents:
            read_paths.add(path)
        return content

    def require_complete_preflight(_method, _url, _kwargs):
        assert read_paths == {source / name for name in files}

    provider = ScriptedVercel(
        *upload_success_script(tuple(expected_digests)),
        before_call=require_complete_preflight,
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)
    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == DEPLOYMENT_ID
    assert DEPLOYMENT_URL in outcome.artifact_refs
    assert_confirmed_digests(outcome, sorted(expected_digests))
    deployment_call = provider.matching("POST", "/v13/deployments")[0]
    manifest = deployment_call[2]["json"]["files"]
    assert {item["sha"] for item in manifest} == expected_digests
    assert {item["file"] for item in manifest} == set(files)
    blob_calls = provider.matching("POST", "/v2/files")
    assert len(blob_calls) == len(files)
    assert {call[2]["headers"]["x-vercel-digest"] for call in blob_calls} == expected_digests
    assert_deployment_posted_once(provider)
    assert_no_implicit_project_patch(provider)
    provider.assert_done()


@pytest.mark.asyncio
async def test_upload_unreadable_file_fails_before_any_provider_call(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root, source = create_workspace(
        tmp_path,
        {"readable.txt": b"ok", "unreadable.txt": b"blocked"},
    )
    original_read_bytes = Path.read_bytes
    provider = ScriptedVercel()

    def guarded_read_bytes(path: Path) -> bytes:
        if path == source / "unreadable.txt":
            raise PermissionError("unreadable fixture")
        return original_read_bytes(path)

    install_vercel(monkeypatch, provider, workspace_root=workspace_root)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    assert_outcome(result, "failed")
    assert provider.calls == []


@pytest.mark.asyncio
async def test_upload_receipt_metadata_limit_fails_before_provider_io(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root, _source = create_workspace(
        tmp_path,
        {
            f"file-{index:03d}.txt": f"unique-{index}".encode()
            for index in range(300)
        },
    )
    provider = ScriptedVercel()
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    assert_outcome(
        result,
        "failed",
        error_code="vercel_deploy_receipt_limit_exceeded",
    )
    assert provider.factory_calls == 0
    assert provider.calls == []


@pytest.mark.parametrize("status_code", (401, 403, 429, 500))
@pytest.mark.asyncio
async def test_project_lookup_only_explicit_404_may_create(
    monkeypatch,
    tmp_path,
    status_code,
) -> None:
    workspace_root, _source = create_workspace(tmp_path, {"index.html": b"ok"})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(status_code, {"error": {"code": "LOOKUP_FAILED"}}),
        )
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, "failed")
    assert outcome.retryable is False
    assert provider.count("POST", "/v9/projects") == 0
    assert_no_deployment_post(provider)
    provider.assert_done()


@pytest.mark.asyncio
async def test_project_404_creates_once_with_receipt_then_deploys(
    monkeypatch,
    tmp_path,
) -> None:
    content = b"hello"
    digest = sha1(content)
    workspace_root, _source = create_workspace(tmp_path, {"index.html": content})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(404, {"error": {"code": "not_found"}}),
        ),
        ExpectedCall(
            "POST",
            "/v9/projects",
            FakeResponse(201, project_receipt()),
        ),
        ExpectedCall("POST", "/v2/files", FakeResponse(200, {})),
        ExpectedCall(
            "POST",
            "/v13/deployments",
            FakeResponse(201, deployment_receipt("QUEUED")),
        ),
        ExpectedCall(
            "GET",
            f"/v13/deployments/{DEPLOYMENT_ID}",
            FakeResponse(200, deployment_receipt("READY")),
        ),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == DEPLOYMENT_ID
    assert outcome.metadata.get("project_id") == PROJECT_ID
    assert_confirmed_digests(outcome, [digest])
    assert provider.count("POST", "/v9/projects") == 1
    assert_deployment_posted_once(provider)
    assert_no_implicit_project_patch(provider)
    provider.assert_done()


@pytest.mark.parametrize(
    ("create_result", "expected_status"),
    (
        (FakeResponse(400, {"error": {"code": "INVALID_PROJECT"}}), "failed"),
        (httpx.ReadTimeout("project create response lost"), "unknown"),
        (FakeResponse(201, {}), "unknown"),
        (FakeResponse(201, json_error=ValueError("bad JSON")), "unknown"),
    ),
    ids=["known-4xx", "timeout", "missing-receipt", "bad-json"],
)
@pytest.mark.asyncio
async def test_project_create_stage_settles_without_downstream_replay(
    monkeypatch,
    tmp_path,
    create_result,
    expected_status,
) -> None:
    workspace_root, _source = create_workspace(tmp_path, {"index.html": b"ok"})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(404, {"error": {"code": "not_found"}}),
        ),
        ExpectedCall("POST", "/v9/projects", create_result),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref is None
    assert provider.count("POST", "/v9/projects") == 1
    assert provider.count("POST", "/v2/files") == 0
    assert_no_deployment_post(provider)
    if expected_status == "unknown":
        assert outcome.retryable is False
    provider.assert_done()


@pytest.mark.parametrize(
    ("second_blob_result", "expected_status"),
    (
        (FakeResponse(400, {"error": {"code": "BLOB_REJECTED"}}), "failed"),
        (httpx.ReadTimeout("blob response lost"), "unknown"),
    ),
    ids=["known-4xx", "timeout"],
)
@pytest.mark.asyncio
async def test_blob_stage_preserves_confirmed_content_addressed_receipts(
    monkeypatch,
    tmp_path,
    second_blob_result,
    expected_status,
) -> None:
    first = b"first"
    second = b"second"
    first_digest = sha1(first)
    workspace_root, _source = create_workspace(
        tmp_path,
        {"a.txt": first, "b.txt": second},
    )
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall("POST", "/v2/files", FakeResponse(200, {})),
        ExpectedCall("POST", "/v2/files", second_blob_result),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref == PROJECT_ID
    assert_confirmed_digests(outcome, [first_digest])
    assert provider.count("POST", "/v2/files") == 2
    assert_no_deployment_post(provider)
    if expected_status == "unknown":
        assert outcome.retryable is False
    provider.assert_done()


@pytest.mark.parametrize(
    ("deployment_result", "expected_status"),
    (
        (FakeResponse(400, {"error": {"code": "INVALID_DEPLOYMENT"}}), "failed"),
        (httpx.ReadTimeout("deployment response lost"), "unknown"),
        (FakeResponse(201, {}), "unknown"),
        (FakeResponse(201, {"id": DEPLOYMENT_ID}), "unknown"),
        (FakeResponse(201, {"url": DEPLOYMENT_HOST}), "unknown"),
        (FakeResponse(201, json_error=ValueError("bad JSON")), "unknown"),
    ),
    ids=[
        "known-4xx",
        "timeout",
        "missing-receipt",
        "missing-url",
        "missing-id",
        "bad-json",
    ],
)
@pytest.mark.asyncio
async def test_deployment_post_settles_once_and_preserves_prior_stage_receipts(
    monkeypatch,
    tmp_path,
    deployment_result,
    expected_status,
) -> None:
    content = b"hello"
    digest = sha1(content)
    workspace_root, _source = create_workspace(tmp_path, {"index.html": content})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall("POST", "/v2/files", FakeResponse(200, {})),
        ExpectedCall("POST", "/v13/deployments", deployment_result),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref == PROJECT_ID
    assert_confirmed_digests(outcome, [digest])
    assert_deployment_posted_once(provider)
    assert provider.count("GET", f"/v13/deployments/{DEPLOYMENT_ID}") == 0
    if expected_status == "unknown":
        assert outcome.retryable is False
    provider.assert_done()


@pytest.mark.parametrize(
    "unsafe_url",
    ("http://app-abc.vercel.app", "javascript:alert(1)"),
)
@pytest.mark.asyncio
async def test_deployment_post_rejects_non_https_artifact_receipt(
    monkeypatch,
    tmp_path,
    unsafe_url,
) -> None:
    content = b"hello"
    digest = sha1(content)
    workspace_root, _source = create_workspace(tmp_path, {"index.html": content})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall("POST", "/v2/files", FakeResponse(200, {})),
        ExpectedCall(
            "POST",
            "/v13/deployments",
            FakeResponse(
                201,
                {
                    "id": DEPLOYMENT_ID,
                    "url": unsafe_url,
                    "readyState": "QUEUED",
                },
            ),
        ),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(
        result,
        "unknown",
        error_code="vercel_deployment_create_outcome_unknown",
    )
    assert outcome.result_ref == PROJECT_ID
    assert outcome.artifact_refs == ()
    assert_confirmed_digests(outcome, [digest])
    assert_deployment_posted_once(provider)
    assert provider.count("GET", f"/v13/deployments/{DEPLOYMENT_ID}") == 0
    provider.assert_done()


@pytest.mark.asyncio
async def test_accepted_building_then_poll_timeout_is_successful_pending_receipt(
    monkeypatch,
    tmp_path,
) -> None:
    content = b"hello"
    digest = sha1(content)
    workspace_root, _source = create_workspace(tmp_path, {"index.html": content})
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall("POST", "/v2/files", FakeResponse(200, {})),
        ExpectedCall(
            "POST",
            "/v13/deployments",
            FakeResponse(201, deployment_receipt("BUILDING")),
        ),
        ExpectedCall(
            "GET",
            f"/v13/deployments/{DEPLOYMENT_ID}",
            httpx.ReadTimeout("poll timed out"),
        ),
    )
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == DEPLOYMENT_ID
    assert DEPLOYMENT_URL in outcome.artifact_refs
    assert outcome.metadata.get("deployment_state") in {"BUILDING", "PENDING"}
    assert_confirmed_digests(outcome, [digest])
    assert_deployment_posted_once(provider)
    assert provider.count("GET", f"/v13/deployments/{DEPLOYMENT_ID}") == 1
    assert_no_implicit_project_patch(provider)
    provider.assert_done()


@pytest.mark.parametrize(
    ("final_state", "expected_status"),
    (
        ("READY", "succeeded"),
        ("ERROR", "failed"),
        ("CANCELED", "failed"),
    ),
)
@pytest.mark.asyncio
async def test_deployment_poll_settles_known_terminal_state_without_repost(
    monkeypatch,
    tmp_path,
    final_state,
    expected_status,
) -> None:
    content = b"hello"
    workspace_root, _source = create_workspace(tmp_path, {"index.html": content})
    provider = ScriptedVercel(*upload_success_script((sha1(content),), final_state=final_state))
    install_vercel(monkeypatch, provider, workspace_root=workspace_root)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "upload",
            "source_dir": "workspace/site",
        }
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref == DEPLOYMENT_ID
    assert outcome.metadata.get("deployment_state") == final_state
    assert_deployment_posted_once(provider)
    assert provider.count("GET", f"/v13/deployments/{DEPLOYMENT_ID}") == 1
    if expected_status == "failed":
        assert outcome.retryable is False
        assert final_state.lower() in (outcome.error_code or "").lower()
        assert outcome.artifact_refs == (DEPLOYMENT_URL,)
        assert outcome.evidence_refs == (
            f"vercel-deployment://{DEPLOYMENT_ID}",
        )
    assert_no_implicit_project_patch(provider)
    provider.assert_done()


def github_success_script(
    *,
    link_result: object | None = None,
    reconcile_result: object | None = None,
) -> list[ExpectedCall]:
    script = [
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall(
            "POST",
            f"/v9/projects/{PROJECT_NAME}/link",
            link_result or FakeResponse(200, {"type": "github", "repo": "owner/repo"}),
        ),
    ]
    if reconcile_result is not None:
        script.append(
            ExpectedCall(
                "GET",
                f"/v9/projects/{PROJECT_NAME}",
                reconcile_result,
            )
        )
    script.extend(
        [
            ExpectedCall(
                "POST",
                "/v13/deployments",
                FakeResponse(201, deployment_receipt("QUEUED")),
            ),
            ExpectedCall(
                "GET",
                f"/v13/deployments/{DEPLOYMENT_ID}",
                FakeResponse(200, deployment_receipt("READY")),
            ),
        ]
    )
    return script


@pytest.mark.asyncio
async def test_github_mode_deploys_existing_repo_ref_without_workspace_or_push_claim(
    monkeypatch,
    tmp_path,
) -> None:
    missing_workspace = tmp_path / "workspace-does-not-exist"
    provider = ScriptedVercel(*github_success_script())
    install_vercel(monkeypatch, provider, workspace_root=missing_workspace)

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "github",
            "github_repo": "owner/repo",
            "git_ref": "release-2026-07",
        }
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == DEPLOYMENT_ID
    assert "push" not in (outcome.summary or "").lower()
    assert provider.count("POST", "/v2/files") == 0
    deployment_payload = provider.matching("POST", "/v13/deployments")[0][2]["json"]
    assert deployment_payload["gitSource"] == {
        "type": "github",
        "repo": "owner/repo",
        "ref": "release-2026-07",
    }
    assert_deployment_posted_once(provider)
    assert_no_implicit_project_patch(provider)
    provider.assert_done()


@pytest.mark.asyncio
async def test_github_link_structured_409_reconciles_matching_repo_before_deploy(
    monkeypatch,
    tmp_path,
) -> None:
    conflict = FakeResponse(
        409,
        {"error": {"code": "PROJECT_ALREADY_LINKED"}},
    )
    reconciled = FakeResponse(
        200,
        project_receipt(link={"type": "github", "repo": "owner/repo"}),
    )
    provider = ScriptedVercel(
        *github_success_script(
            link_result=conflict,
            reconcile_result=reconciled,
        )
    )
    install_vercel(
        monkeypatch,
        provider,
        workspace_root=tmp_path / "missing-workspace",
    )

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "github",
            "github_repo": "owner/repo",
            "git_ref": "main",
        }
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == DEPLOYMENT_ID
    assert outcome.metadata.get("linked_repo") == "owner/repo"
    assert provider.count("POST", f"/v9/projects/{PROJECT_NAME}/link") == 1
    assert provider.count("GET", f"/v9/projects/{PROJECT_NAME}") == 2
    assert_deployment_posted_once(provider)
    provider.assert_done()


@pytest.mark.asyncio
async def test_github_link_409_mismatch_is_failed_before_deployment(
    monkeypatch,
    tmp_path,
) -> None:
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall(
            "POST",
            f"/v9/projects/{PROJECT_NAME}/link",
            FakeResponse(409, {"error": {"code": "PROJECT_ALREADY_LINKED"}}),
        ),
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(
                200,
                project_receipt(link={"type": "github", "repo": "someone/else"}),
            ),
        ),
    )
    install_vercel(
        monkeypatch,
        provider,
        workspace_root=tmp_path / "missing-workspace",
    )

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "github",
            "github_repo": "owner/repo",
            "git_ref": "main",
        }
    )

    outcome = assert_outcome(result, "failed")
    assert outcome.result_ref == PROJECT_ID
    assert_no_deployment_post(provider)
    provider.assert_done()


@pytest.mark.parametrize(
    ("link_result", "expected_status"),
    (
        (FakeResponse(400, {"error": {"code": "INVALID_LINK"}}), "failed"),
        (httpx.ReadTimeout("link response lost"), "unknown"),
        (FakeResponse(200, {}), "unknown"),
        (
            FakeResponse(200, {"type": "github", "repo": "someone/else"}),
            "unknown",
        ),
        (FakeResponse(409, {"error": {"code": "OTHER_CONFLICT"}}), "failed"),
    ),
    ids=["known-4xx", "timeout", "missing-receipt", "receipt-mismatch", "other-409"],
)
@pytest.mark.asyncio
async def test_github_link_stage_settles_before_deployment_post(
    monkeypatch,
    tmp_path,
    link_result,
    expected_status,
) -> None:
    provider = ScriptedVercel(
        ExpectedCall(
            "GET",
            f"/v9/projects/{PROJECT_NAME}",
            FakeResponse(200, project_receipt()),
        ),
        ExpectedCall(
            "POST",
            f"/v9/projects/{PROJECT_NAME}/link",
            link_result,
        ),
    )
    install_vercel(
        monkeypatch,
        provider,
        workspace_root=tmp_path / "missing-workspace",
    )

    result = await execute(
        {
            "project_name": PROJECT_NAME,
            "deploy_method": "github",
            "github_repo": "owner/repo",
            "git_ref": "main",
        }
    )

    outcome = assert_outcome(result, expected_status)
    assert outcome.result_ref == PROJECT_ID
    if expected_status == "unknown":
        assert outcome.retryable is False
    assert provider.count("POST", f"/v9/projects/{PROJECT_NAME}/link") == 1
    assert_no_deployment_post(provider)
    provider.assert_done()
