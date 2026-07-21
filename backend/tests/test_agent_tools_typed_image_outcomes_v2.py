from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import activity_logger, agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import builtin_model_definition


IMAGE_GENERATION_TOOLS = (
    "generate_image_siliconflow",
    "generate_image_openai",
    "generate_image_google",
    "generate_image_custom",
)

# Complete 1 x 1 PNG. Keeping a real image fixture makes the contract test
# independent of Content-Type claims made by a provider or download endpoint.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/wcAAgAB/ax3ZAAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
MAX_GENERATED_IMAGE_BYTES = 25 * 1024 * 1024


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        content: bytes = b"",
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://images.example.test/result")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


def _ready_config(tool_name: str) -> dict:
    if tool_name == "upload_image":
        return {
            "private_key": "imagekit-secret",
            "url_endpoint": "https://ik.imagekit.io/acme",
        }
    if tool_name == "generate_image_custom":
        return {
            "api_key": "image-secret",
            "base_url": "https://images.example.test/v1",
            "endpoint_path": "/chat/completions",
            "model": "image-model",
            "request_body_template_json": "",
            "response_image_path": (
                "choices.0.message.images.0.image_url.url"
            ),
            "extra_headers_json": "",
            "timeout_seconds": 120,
        }
    return {"api_key": "image-secret"}


def _provider_payload(
    tool_name: str,
    *,
    image_bytes: bytes = PNG_BYTES,
    use_download_url: bool = False,
) -> dict:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    if tool_name in {"generate_image_siliconflow", "generate_image_openai"}:
        image = (
            {"url": "https://images.example.test/generated.png"}
            if use_download_url
            else {"b64_json": encoded}
        )
        return {"data": [image]}
    if tool_name == "generate_image_google":
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": encoded,
                                }
                            }
                        ]
                    }
                }
            ]
        }
    image_ref = (
        "https://images.example.test/generated.png"
        if use_download_url
        else f"data:image/png;base64,{encoded}"
    )
    return {
        "choices": [
            {
                "message": {
                    "images": [{"image_url": {"url": image_ref}}]
                }
            }
        ]
    }


def _install_provider_http_fake(
    monkeypatch,
    tool_name: str,
    scenario: str,
    calls: dict[str, int],
) -> None:
    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            calls["post"] = calls.get("post", 0) + 1
            if scenario == "timeout":
                raise httpx.TimeoutException("generation timed out")
            if scenario == "4xx":
                return FakeResponse(400, {"error": {"message": "bad request"}}, text="bad request")
            if scenario == "5xx":
                return FakeResponse(503, {"error": {"message": "unavailable"}}, text="unavailable")
            if scenario == "malformed_success":
                return FakeResponse(200, {})

            use_download = scenario in {
                "download_error",
                "download_non_image",
                "download_too_large",
            }
            inline_bytes = (
                b"<html>not an image</html>"
                if scenario == "inline_non_image"
                else (
                    PNG_BYTES + b"x" * MAX_GENERATED_IMAGE_BYTES
                    if scenario == "inline_too_large"
                    else PNG_BYTES
                )
            )
            return FakeResponse(
                200,
                _provider_payload(
                    tool_name,
                    image_bytes=inline_bytes,
                    use_download_url=use_download,
                ),
            )

        async def get(self, *args, **kwargs):
            calls["get"] = calls.get("get", 0) + 1
            if scenario == "download_error":
                raise httpx.TimeoutException("download timed out")
            if scenario == "download_non_image":
                return FakeResponse(
                    200,
                    content=b"<html>not an image</html>",
                    headers={"content-type": "image/png"},
                )
            if scenario == "download_too_large":
                return FakeResponse(
                    200,
                    content=PNG_BYTES + b"x" * MAX_GENERATED_IMAGE_BYTES,
                    headers={"content-type": "image/png"},
                )
            return FakeResponse(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", Client)


async def _execute_generate(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    arguments: dict,
    *,
    scenario: str = "success",
    sync_error: Exception | None = None,
    workspace_name: str = "agent-workspace",
) -> tuple[ToolExecutionOutcome | str, dict[str, int], Path]:
    workspace = tmp_path / workspace_name
    workspace.mkdir(parents=True, exist_ok=True)
    calls: dict[str, int] = {"post": 0, "get": 0, "flush": 0}
    _install_provider_http_fake(monkeypatch, tool_name, scenario, calls)

    async def tenant_id(_agent_id):
        return "tenant-1"

    async def config(_agent_id, requested_name):
        return _ready_config(requested_name)

    async def prepare(*args, **kwargs):
        return SimpleNamespace(root=workspace, cleanup=lambda: None)

    async def flush(*args, **kwargs):
        calls["flush"] += 1
        if sync_error is not None:
            raise sync_error
        return {
            "updated": [arguments.get("save_path", "workspace/images/generated.png")],
            "deleted": [],
            "conflicted": [],
        }

    async def no_activity(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_id)
    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", prepare)
    monkeypatch.setattr(agent_tools, "flush_temp_workspace", flush)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)

    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        uuid.uuid4(),
        uuid.uuid4(),
    )
    return outcome, calls, workspace


def test_image_contracts_validate_sources_prompt_size_and_save_path() -> None:
    upload = builtin_model_definition("upload_image")["function"]["parameters"]
    assert upload.get("oneOf") == [
        {"required": ["file_path"]},
        {"required": ["url"]},
    ]
    assert "anyOf" not in upload
    assert upload["properties"]["url"]["format"] == "uri"

    for tool_name in IMAGE_GENERATION_TOOLS:
        schema = builtin_model_definition(tool_name)["function"]["parameters"]
        assert schema["properties"]["prompt"]["minLength"] == 1
        assert "1024x1024" in schema["properties"]["size"]["enum"]
        assert schema["properties"]["save_path"]["pattern"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "expected_status"),
    [
        (400, {"message": "bad request"}, "failed"),
        (503, {"message": "temporarily unavailable"}, "unknown"),
        (
            201,
            {
                "url": "javascript:alert(1)",
                "fileId": "file-1",
                "name": "bad.png",
            },
            "unknown",
        ),
        (
            201,
            {"url": "https://ik.imagekit.io/acme/incomplete.png"},
            "unknown",
        ),
    ],
)
async def test_upload_image_classifies_provider_receipts(
    monkeypatch,
    tmp_path: Path,
    status_code: int,
    payload: dict,
    expected_status: str,
) -> None:
    calls = {"post": 0}

    async def configured(*args, **kwargs):
        return _ready_config("upload_image")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            calls["post"] += 1
            return FakeResponse(status_code, payload, text="provider response")

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    outcome = await agent_tools._upload_image_outcome(
        uuid.uuid4(),
        tmp_path,
        {"url": "https://source.example.test/image.png"},
    )

    assert outcome.status == expected_status
    assert outcome.retryable is False
    assert calls["post"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {
            "file_path": "workspace/source.png",
            "url": "https://source.example.test/source.png",
        },
        {"url": "not-a-public-http-url"},
        {"url": "file:///etc/passwd"},
    ],
)
async def test_upload_image_rejects_ambiguous_or_invalid_sources_before_dispatch(
    monkeypatch,
    tmp_path: Path,
    arguments: dict,
) -> None:
    calls = {"post": 0}

    async def configured(*args, **kwargs):
        return _ready_config("upload_image")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            calls["post"] += 1
            return FakeResponse(201, {})

    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "source.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    outcome = await agent_tools._upload_image_outcome(
        uuid.uuid4(),
        tmp_path,
        arguments,
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "invalid_tool_arguments"
    assert calls["post"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
async def test_each_generate_tool_has_local_readiness_and_typed_visibility(
    monkeypatch,
    tool_name: str,
) -> None:
    async def assigned(_agent_id):
        return [builtin_model_definition(tool_name)]

    async def no_dynamic_mcp(_agent_id):
        return set()

    async def configured(_agent_id, requested_name):
        return _ready_config(requested_name)

    class ProviderCallForbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("readiness must not ping the image provider")

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    monkeypatch.setattr(httpx, "AsyncClient", ProviderCallForbidden)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert tool_name in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert [tool["function"]["name"] for tool in resolved] == [tool_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
async def test_each_generate_tool_is_hidden_when_its_local_config_is_incomplete(
    monkeypatch,
    tool_name: str,
) -> None:
    async def assigned(_agent_id):
        return [builtin_model_definition(tool_name)]

    async def no_dynamic_mcp(_agent_id):
        return set()

    async def missing_config(_agent_id, _requested_name):
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", missing_config)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert resolved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
@pytest.mark.parametrize(
    "arguments",
    [
        {"prompt": "   ", "save_path": "workspace/images/result.png"},
        {
            "prompt": "a quiet mountain",
            "size": "unbounded",
            "save_path": "workspace/images/result.png",
        },
        {"prompt": "a quiet mountain", "save_path": "/tmp/result.png"},
        {"prompt": "a quiet mountain", "save_path": "../result.png"},
        {"prompt": "a quiet mountain", "save_path": "workspace/result.txt"},
    ],
)
async def test_generate_validation_fails_before_provider_dispatch(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    arguments: dict,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        tool_name,
        arguments,
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code in {"invalid_tool_arguments", "workspace_path_invalid"}
    assert calls["post"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("4xx", "failed"),
        ("5xx", "unknown"),
        ("timeout", "unknown"),
        ("malformed_success", "unknown"),
    ],
)
async def test_generate_provider_response_has_a_typed_settlement_boundary(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    scenario: str,
    expected_status: str,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        tool_name,
        {
            "prompt": "a quiet mountain",
            "size": "1024x1024",
            "save_path": "workspace/images/result.png",
        },
        scenario=scenario,
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == expected_status
    assert outcome.retryable is False
    assert calls["post"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    (
        "generate_image_siliconflow",
        "generate_image_openai",
        "generate_image_custom",
    ),
)
async def test_download_failure_after_generation_is_unknown_without_regeneration(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        tool_name,
        {
            "prompt": "a quiet mountain",
            "save_path": "workspace/images/result.png",
        },
        scenario="download_error",
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "unknown"
    assert outcome.retryable is False
    assert calls["post"] == 1
    assert calls["get"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
async def test_non_image_payload_after_generation_is_unknown_without_regeneration(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    scenario = (
        "download_non_image"
        if tool_name != "generate_image_google"
        else "inline_non_image"
    )
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        tool_name,
        {
            "prompt": "a quiet mountain",
            "save_path": "workspace/images/result.png",
        },
        scenario=scenario,
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "unknown"
    assert calls["post"] == 1


@pytest.mark.asyncio
async def test_oversized_payload_after_generation_is_unknown_without_regeneration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        "generate_image_openai",
        {
            "prompt": "a quiet mountain",
            "save_path": "workspace/images/result.png",
        },
        scenario="download_too_large",
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "unknown"
    assert outcome.retryable is False
    assert calls["post"] == 1


@pytest.mark.asyncio
async def test_write_failure_after_generation_is_unknown_without_regeneration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    real_write_bytes = Path.write_bytes

    def fail_generated_write(path: Path, data: bytes) -> int:
        if path.name == "result.png":
            raise OSError("disk write failed")
        return real_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_generated_write)
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        "generate_image_openai",
        {
            "prompt": "a quiet mountain",
            "save_path": "workspace/images/result.png",
        },
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "unknown"
    assert outcome.retryable is False
    assert calls["post"] == 1


@pytest.mark.asyncio
async def test_sync_failure_after_generation_is_unknown_without_regeneration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        "generate_image_openai",
        {
            "prompt": "a quiet mountain",
            "save_path": "workspace/images/result.png",
        },
        sync_error=OSError("storage sync failed"),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "unknown"
    assert outcome.error_code == "workspace_sync_outcome_unknown"
    assert outcome.retryable is False
    assert calls["post"] == 1
    assert calls["flush"] == 1


@pytest.mark.asyncio
async def test_generate_rejects_string_prefix_sibling_escape_before_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        "generate_image_openai",
        {
            "prompt": "a quiet mountain",
            "save_path": "../agent-workspace-escape/result.png",
        },
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "workspace_path_invalid"
    assert calls["post"] == 0


@pytest.mark.asyncio
async def test_generate_rejects_symlink_escape_before_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    outcome, calls, _workspace = await _execute_generate(
        monkeypatch,
        tmp_path,
        "generate_image_openai",
        {
            "prompt": "a quiet mountain",
            "save_path": "escape/result.png",
        },
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "workspace_path_invalid"
    assert calls["post"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", IMAGE_GENERATION_TOOLS)
async def test_generate_success_returns_workspace_artifact_and_content_hash(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    agent_id = uuid.uuid4()
    save_path = "workspace/images/result.png"
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    calls: dict[str, int] = {"post": 0, "get": 0, "flush": 0}
    _install_provider_http_fake(monkeypatch, tool_name, "success", calls)

    async def tenant_id(_agent_id):
        return "tenant-1"

    async def config(_agent_id, requested_name):
        return _ready_config(requested_name)

    async def prepare(*args, **kwargs):
        return SimpleNamespace(root=workspace, cleanup=lambda: None)

    async def flush(*args, **kwargs):
        calls["flush"] += 1
        return {"updated": [save_path], "deleted": [], "conflicted": []}

    async def no_activity(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_id)
    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", prepare)
    monkeypatch.setattr(agent_tools, "flush_temp_workspace", flush)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)

    outcome = await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        {
            "prompt": "a quiet mountain",
            "size": "1024x1024",
            "save_path": save_path,
        },
        agent_id,
        uuid.uuid4(),
    )

    expected_ref = f"workspace://{agent_id}/{save_path}"
    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "succeeded"
    assert outcome.result_ref == expected_ref
    assert outcome.artifact_refs == (expected_ref,)
    assert outcome.metadata["content_hash"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert (workspace / save_path).read_bytes() == PNG_BYTES
    assert calls["post"] == 1
    assert calls["flush"] == 1
