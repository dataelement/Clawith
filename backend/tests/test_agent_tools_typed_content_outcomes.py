from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile

import httpx
import pytest

from app.services import agent_tools
from app.services.builtin_tool_definitions import builtin_model_definition


@pytest.mark.asyncio
async def test_runtime_resolver_hides_upload_image_until_credentials_exist(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tools = [
        builtin_model_definition("read_webpage"),
        builtin_model_definition("upload_image"),
    ]

    async def fake_tools(_agent_id):
        return tools

    async def missing_config(_agent_id, _name):
        return {}

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", fake_tools)
    monkeypatch.setattr(agent_tools, "_get_tool_config", missing_config)

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(agent_id)
    assert [tool["function"]["name"] for tool in resolved] == ["read_webpage"]

    async def configured(_agent_id, _name):
        return {"private_key": "configured"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)
    resolved = await agent_tools.get_runtime_agent_tools_for_llm(agent_id)
    assert [tool["function"]["name"] for tool in resolved] == [
        "read_webpage",
        "upload_image",
    ]


@pytest.mark.asyncio
async def test_conversion_uses_validated_artifact_not_converter_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    agent_id = uuid.uuid4()
    source = tmp_path / "source.csv"
    source.write_text("name\nAda\n", encoding="utf-8")

    async def text_only_success(_agent_id, _ws, _arguments):
        return "success"

    monkeypatch.setattr(agent_tools, "_convert_csv_to_xlsx", text_only_success)
    failed = await agent_tools._convert_file_outcome(
        agent_id,
        tmp_path,
        {"source_path": "source.csv", "target_path": "result.xlsx"},
        tool_name="convert_csv_to_xlsx",
    )
    assert failed.status == "failed"
    assert failed.error_code == "conversion_artifact_invalid"

    async def validated_artifact(_agent_id, ws, arguments):
        target = ws / arguments["target_path"]
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("[Content_Types].xml", "types")
            archive.writestr("xl/workbook.xml", "workbook")
        return "failure-looking display text is not interpreted"

    monkeypatch.setattr(agent_tools, "_convert_csv_to_xlsx", validated_artifact)
    succeeded = await agent_tools._convert_file_outcome(
        agent_id,
        tmp_path,
        {"source_path": "source.csv", "target_path": "result.xlsx"},
        tool_name="convert_csv_to_xlsx",
    )
    assert succeeded.status == "succeeded"
    assert succeeded.artifact_refs == (f"workspace://{agent_id}/result.xlsx",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "converter_name", "target_name", "archive_member"),
    [
        ("convert_csv_to_xlsx", "_convert_csv_to_xlsx", "result.xlsx", "xl/workbook.xml"),
        ("convert_html_to_pdf", "_convert_html_to_pdf", "result.pdf", None),
        ("convert_html_to_pptx", "_convert_html_to_pptx", "result.pptx", "ppt/presentation.xml"),
        ("convert_markdown_to_docx", "_convert_markdown_to_docx", "result.docx", "word/document.xml"),
        ("convert_markdown_to_pdf", "_convert_markdown_to_pdf", "result.pdf", None),
    ],
)
async def test_each_conversion_family_emits_a_validated_workspace_ref(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    converter_name: str,
    target_name: str,
    archive_member: str | None,
) -> None:
    agent_id = uuid.uuid4()
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")

    async def converter(_agent_id, ws, arguments):
        target = ws / arguments["target_path"]
        if archive_member is None:
            target.write_bytes(b"%PDF-1.7\nbody\n%%EOF")
        else:
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("[Content_Types].xml", "types")
                archive.writestr(archive_member, "content")
        return "display text"

    monkeypatch.setattr(agent_tools, converter_name, converter)
    outcome = await agent_tools._convert_file_outcome(
        agent_id,
        tmp_path,
        {"source_path": "source.txt", "target_path": target_name},
        tool_name=tool_name,
    )
    assert outcome.status == "succeeded"
    assert outcome.artifact_refs == (f"workspace://{agent_id}/{target_name}",)


def test_document_reader_returns_structured_parse_fact(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("verified content", encoding="utf-8")
    success = agent_tools._read_document_sync(tmp_path, "notes.txt")
    assert success.ok is True
    assert success.content == "verified content"

    (tmp_path / "notes.bin").write_bytes(b"opaque")
    failure = agent_tools._read_document_sync(tmp_path, "notes.bin")
    assert failure.ok is False
    assert failure.error_code == "document_format_unsupported"


@pytest.mark.asyncio
async def test_document_process_boundary_preserves_structured_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text("process result", encoding="utf-8")
    result = await agent_tools._read_document_result(tmp_path, "notes.txt")
    assert result == agent_tools.DocumentReadResult(True, "process result")


@pytest.mark.asyncio
async def test_document_outcome_preserves_workspace_evidence(monkeypatch) -> None:
    agent_id = uuid.uuid4()

    class TempWorkspace:
        root = Path("/tmp/typed-document-test")

        def cleanup(self):
            return None

    async def prepare(*args, **kwargs):
        return TempWorkspace()

    async def read_result(*args, **kwargs):
        return agent_tools.DocumentReadResult(True, "document body")

    monkeypatch.setattr(agent_tools, "_prepare_temp_workspace", prepare)
    monkeypatch.setattr(agent_tools, "_read_document_result", read_result)

    outcome = await agent_tools._read_document_outcome(
        agent_id,
        {"path": "workspace/report.pdf"},
        tenant_id=None,
    )
    assert outcome.status == "succeeded"
    assert outcome.evidence_refs == (f"workspace://{agent_id}/workspace/report.pdf",)


@pytest.mark.asyncio
async def test_read_webpage_uses_http_fact_and_marks_read_timeout_retryable(
    monkeypatch,
) -> None:
    requested_url = "https://example.test/source"
    final_url = "https://example.test/final"

    async def validate(url):
        return url, None

    monkeypatch.setattr(agent_tools, "_validate_public_http_url", validate)

    class Response:
        status_code = 200
        url = final_url
        encoding = "utf-8"
        headers = {"content-type": "text/plain"}

        async def aiter_bytes(self):
            yield b"provider body"

    class StreamContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return False

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *args, **kwargs):
            return StreamContext()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    success = await agent_tools._read_webpage_outcome({"url": requested_url})
    assert success.status == "succeeded"
    assert success.evidence_refs == (final_url,)

    class TimeoutStreamContext:
        async def __aenter__(self):
            raise httpx.TimeoutException("timeout")

        async def __aexit__(self, *_args):
            return False

    class TimeoutClient(Client):
        def stream(self, *args, **kwargs):
            return TimeoutStreamContext()

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    timeout = await agent_tools._read_webpage_outcome({"url": requested_url})
    assert timeout.status == "failed"
    assert timeout.error_code == "webpage_timeout"
    assert timeout.retryable is True


@pytest.mark.asyncio
async def test_execute_code_uses_exit_code_and_never_reexecutes_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import app.config as config_module
    from app.services.sandbox import registry

    config = SimpleNamespace(max_timeout=60, allow_network=False)
    monkeypatch.setattr(config_module, "get_sandbox_config", lambda: config)

    async def no_agent_config(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_tools, "_get_tool_config", no_agent_config)

    class Backend:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error

        async def execute(self, **kwargs):
            if self.error:
                raise self.error
            return self.result

        def _format_result(self, result):
            return f"exit={result.exit_code}"

    backend = Backend(SimpleNamespace(success=True, exit_code=0))
    monkeypatch.setattr(registry, "get_sandbox_backend", lambda _config: backend)
    success = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('ok')"},
    )
    assert success.status == "succeeded"

    backend.result = SimpleNamespace(success=False, exit_code=7)
    failed = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "raise SystemExit(7)"},
    )
    assert failed.status == "failed"
    assert failed.error_code == "sandbox_execution_failed"

    backend.error = ValueError("transport lost after dispatch")

    async def forbidden_fallback(*args, **kwargs):
        raise AssertionError("an unknown execution must not be re-executed")

    monkeypatch.setattr(
        agent_tools,
        "_execute_code_legacy_outcome",
        forbidden_fallback,
    )
    unknown = await agent_tools._execute_code_outcome(
        uuid.uuid4(),
        tmp_path,
        {"language": "python", "code": "print('maybe ran')"},
    )
    assert unknown.status == "unknown"
    assert unknown.error_code == "sandbox_execution_outcome_unknown"


@pytest.mark.asyncio
async def test_upload_image_uses_provider_response_and_timeout_is_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def configured(*args, **kwargs):
        return {
            "private_key": "secret",
            "url_endpoint": "https://ik.imagekit.io/acme",
        }

    monkeypatch.setattr(agent_tools, "_get_tool_config", configured)

    class Response:
        status_code = 201
        text = ""

        def json(self):
            return {
                "url": "https://ik.imagekit.io/acme/picture.png",
                "fileId": "file-123",
                "size": 2048,
                "name": "picture.png",
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    success = await agent_tools._upload_image_outcome(
        uuid.uuid4(),
        tmp_path,
        {"url": "https://source.example/picture.png"},
    )
    assert success.status == "succeeded"
    assert success.result_ref == "imagekit://file-123"
    assert success.artifact_refs == ("imagekit://file-123",)
    assert success.evidence_refs == ("https://ik.imagekit.io/acme/picture.png",)

    class TimeoutClient(Client):
        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    unknown = await agent_tools._upload_image_outcome(
        uuid.uuid4(),
        tmp_path,
        {"url": "https://source.example/picture.png"},
    )
    assert unknown.status == "unknown"
    assert unknown.error_code == "imagekit_upload_outcome_unknown"


@pytest.mark.asyncio
async def test_publish_page_success_and_commit_ambiguity_are_typed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import app.config as config_module

    class Storage:
        async def exists(self, _key):
            return True

        async def is_file(self, _key):
            return True

        async def read_text(self, *args, **kwargs):
            return "<title>Verified</title>"

    class ScalarResult:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self, fail_commit=False):
            self.fail_commit = fail_commit

        async def execute(self, _statement):
            return ScalarResult()

        def add(self, _page):
            return None

        async def commit(self):
            if self.fail_commit:
                raise RuntimeError("commit response lost")

    class SessionContext:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: Storage())
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(PUBLIC_BASE_URL="https://pages.example"),
    )

    async def public_url(url):
        return url, None

    monkeypatch.setattr(agent_tools, "_validate_public_http_url", public_url)

    db = DB()
    monkeypatch.setattr(agent_tools, "async_session", lambda: SessionContext(db))
    success = await agent_tools._publish_page_outcome(
        uuid.uuid4(),
        uuid.uuid4(),
        tmp_path,
        {"path": "workspace/page.html"},
    )
    assert success.status == "succeeded"
    assert success.result_ref.startswith("published-page://")
    assert success.artifact_refs == (success.result_ref,)
    assert success.evidence_refs[0].startswith("https://pages.example/p/")

    async def non_public_url(_url):
        return None, "private URL"

    monkeypatch.setattr(
        agent_tools,
        "_validate_public_http_url",
        non_public_url,
    )
    safe_without_url = await agent_tools._publish_page_outcome(
        uuid.uuid4(),
        uuid.uuid4(),
        tmp_path,
        {"path": "workspace/page.html"},
    )
    assert safe_without_url.status == "succeeded"
    assert safe_without_url.artifact_refs
    assert safe_without_url.evidence_refs == ()

    failing_db = DB(fail_commit=True)
    monkeypatch.setattr(
        agent_tools,
        "async_session",
        lambda: SessionContext(failing_db),
    )
    unknown = await agent_tools._publish_page_outcome(
        uuid.uuid4(),
        uuid.uuid4(),
        tmp_path,
        {"path": "workspace/page.html"},
    )
    assert unknown.status == "unknown"
    assert unknown.error_code == "published_page_outcome_unknown"


@pytest.mark.asyncio
async def test_list_published_pages_read_failure_is_retryable(monkeypatch) -> None:
    class DB:
        async def execute(self, _statement):
            raise RuntimeError("database unavailable")

    class SessionContext:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(agent_tools, "async_session", lambda: SessionContext())
    outcome = await agent_tools._list_published_pages_outcome(uuid.uuid4())
    assert outcome.status == "failed"
    assert outcome.error_code == "published_page_list_failed"
    assert outcome.retryable is True


@pytest.mark.asyncio
async def test_list_published_pages_uses_db_scoped_evidence_refs(monkeypatch) -> None:
    page = SimpleNamespace(
        short_id="page-123",
        title="Verified",
        source_path="workspace/page.html",
        view_count=2,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [page]

    class DB:
        async def execute(self, _statement):
            return Result()

    class SessionContext:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(agent_tools, "async_session", lambda: SessionContext())
    outcome = await agent_tools._list_published_pages_outcome(uuid.uuid4())
    assert outcome.status == "succeeded"
    assert outcome.evidence_refs == ("published-page://page-123",)
