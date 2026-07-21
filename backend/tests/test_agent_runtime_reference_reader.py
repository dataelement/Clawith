from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app.services import agent_tools
from app.services.agent_runtime.node_executor import DefaultRuntimeFinalizer
from app.services.agent_runtime.state import RuntimeContext
from app.services.agent_runtime.verification import (
    RuntimeToolReferenceReader,
    ToolLedgerRuntimeVerifier,
)
from app.services.storage_runtime.base import StorageVersion


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ManyResult:
    def __init__(self, values) -> None:
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _DB:
    def __init__(self, results: deque) -> None:
        self.results = results

    async def execute(self, _statement):
        return self.results.popleft()


def _factory(*results):
    remaining = deque(results)

    @asynccontextmanager
    async def factory():
        yield _DB(remaining)

    return factory


class _Storage:
    def __init__(self, readable_keys: set[str] | None = None) -> None:
        self.readable_keys = readable_keys or set()
        self.checked_keys: list[str] = []
        self.read_keys: list[str] = []

    async def get_version(self, key: str) -> StorageVersion:
        self.checked_keys.append(key)
        return StorageVersion(
            key=key,
            exists=key in self.readable_keys,
            is_dir=False,
            size=1,
        )

    async def read_bytes(self, key: str) -> bytes:
        self.read_keys.append(key)
        if key not in self.readable_keys:
            raise FileNotFoundError(key)
        return b"readable"


def _execution(
    tool_name: str,
    *,
    artifacts: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
):
    return SimpleNamespace(
        status="succeeded",
        tool_name=tool_name,
        result_summary="typed result",
        result_ref=None,
        result_metadata={
            "artifact_refs": list(artifacts),
            "evidence_refs": list(evidence),
        },
    )


@pytest.mark.asyncio
async def test_workspace_reference_is_run_agent_scoped_and_storage_readable() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    reference = f"workspace://{agent_id}/workspace/report.pdf"
    storage_key = f"{agent_id}/workspace/report.pdf"
    storage = _Storage({storage_key})
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("convert_html_to_pdf", artifacts=(reference,))]),
        ),
        storage=storage,  # type: ignore[arg-type]
    )

    assert await reader.reference_exists(reference, tenant_id, run_id) is True
    assert storage.checked_keys == [storage_key]
    assert storage.read_keys == [storage_key]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference_path",
    [
        "enterprise_info/secret.pdf",
        "runtime/tool-results/secret.json",
        "workspace/../../runtime/tool-results/secret.json",
        "workspace/%2e%2e/%2e%2e/runtime/tool-results/secret.json",
    ],
)
async def test_workspace_reference_rejects_shared_private_and_traversal_paths(
    reference_path: str,
) -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    reference = f"workspace://{agent_id}/{reference_path}"
    storage = _Storage({f"{agent_id}/{reference_path}"})
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("execute_code", artifacts=(reference,))]),
        ),
        storage=storage,  # type: ignore[arg-type]
    )

    assert await reader.reference_exists(reference, tenant_id, run_id) is False
    assert storage.checked_keys == []


@pytest.mark.asyncio
async def test_workspace_reference_rejects_cross_agent_cross_run_and_missing_file() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    other_agent_id = uuid.uuid4()
    cross_agent = f"workspace://{other_agent_id}/workspace/report.pdf"
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("read_document", evidence=(cross_agent,))]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    assert await reader.reference_exists(cross_agent, tenant_id, run_id) is False

    missing_scope = RuntimeToolReferenceReader(
        session_factory=_factory(_ScalarResult(None)),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    own_ref = f"workspace://{agent_id}/workspace/report.pdf"
    assert await missing_scope.reference_exists(own_ref, tenant_id, run_id) is False

    missing_file = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("read_document", evidence=(own_ref,))]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    assert await missing_file.reference_exists(own_ref, tenant_id, run_id) is False


@pytest.mark.asyncio
async def test_run_scope_query_binds_tenant_run_and_agent_tenant() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    captured_params: list[dict] = []

    class InspectingDB:
        async def execute(self, statement):
            captured_params.append(statement.compile().params)
            return _ScalarResult(None)

    @asynccontextmanager
    async def factory():
        yield InspectingDB()

    reader = RuntimeToolReferenceReader(
        session_factory=factory,
        storage=_Storage(),  # type: ignore[arg-type]
    )
    reference = f"workspace://{agent_id}/workspace/report.pdf"

    assert await reader.reference_exists(reference, tenant_id, run_id) is False
    assert len(captured_params) == 1
    assert list(captured_params[0].values()).count(tenant_id) == 2
    assert run_id in captured_params[0].values()


@pytest.mark.asyncio
async def test_published_page_reference_requires_scoped_row_and_readable_source() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    reference = "published-page://page-123"
    source_key = f"{agent_id}/workspace/page.html"
    page = SimpleNamespace(
        short_id="page-123",
        agent_id=agent_id,
        tenant_id=tenant_id,
        source_path="workspace/page.html",
    )
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("publish_page", artifacts=(reference,))]),
            _ScalarResult(page),
        ),
        storage=_Storage({source_key}),  # type: ignore[arg-type]
    )

    assert await reader.reference_exists(reference, tenant_id, run_id) is True

    wrong_scope = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("publish_page", artifacts=(reference,))]),
            _ScalarResult(None),
        ),
        storage=_Storage({source_key}),  # type: ignore[arg-type]
    )
    assert await wrong_scope.reference_exists(reference, tenant_id, run_id) is False


class _ImageKitResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class _ImageKitClient:
    response = _ImageKitResponse({})
    error: Exception | None = None
    calls: list[tuple[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, *, auth, headers):
        self.calls.append((url, auth))
        assert headers == {"Accept": "application/json"}
        if self.error is not None:
            raise self.error
        return self.response


def _imagekit_reader(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    file_id: str,
    cdn_url: str,
) -> RuntimeToolReferenceReader:
    artifact = f"imagekit://{file_id}"
    return RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult(
                [
                    _execution(
                        "upload_image",
                        artifacts=(artifact,),
                        evidence=(cdn_url,),
                    )
                ]
            ),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_imagekit_reference_uses_official_detail_read_and_matches_url(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    file_id = "file-123"
    cdn_url = "https://ik.imagekit.io/acme/file.png"

    async def config(*_args, **_kwargs):
        return {"private_key": "private-key"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    _ImageKitClient.error = None
    _ImageKitClient.calls = []
    _ImageKitClient.response = _ImageKitResponse({"fileId": file_id, "url": cdn_url})
    monkeypatch.setattr(httpx, "AsyncClient", _ImageKitClient)
    reader = _imagekit_reader(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        file_id=file_id,
        cdn_url=cdn_url,
    )

    assert await reader.reference_exists(f"imagekit://{file_id}", tenant_id, run_id) is True
    assert _ImageKitClient.calls == [
        (
            "https://api.imagekit.io/v1/files/file-123/details",
            ("private-key", ""),
        )
    ]

    _ImageKitClient.calls = []
    evidence_reader = _imagekit_reader(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        file_id=file_id,
        cdn_url=cdn_url,
    )
    assert await evidence_reader.reference_exists(cdn_url, tenant_id, run_id) is True
    assert len(_ImageKitClient.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "mismatch", "missing_credentials"])
async def test_imagekit_reference_fails_closed_on_uncertain_provider(
    monkeypatch,
    mode: str,
) -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    file_id = "file-123"
    cdn_url = "https://ik.imagekit.io/acme/file.png"

    async def config(*_args, **_kwargs):
        return {} if mode == "missing_credentials" else {"private_key": "key"}

    monkeypatch.setattr(agent_tools, "_get_tool_config", config)
    _ImageKitClient.error = httpx.TimeoutException("timeout") if mode == "timeout" else None
    _ImageKitClient.response = _ImageKitResponse(
        {
            "fileId": file_id,
            "url": ("https://ik.imagekit.io/acme/other.png" if mode == "mismatch" else cdn_url),
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", _ImageKitClient)
    reader = _imagekit_reader(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        file_id=file_id,
        cdn_url=cdn_url,
    )

    assert await reader.reference_exists(f"imagekit://{file_id}", tenant_id, run_id) is False


@pytest.mark.asyncio
async def test_http_evidence_is_ledger_bound_and_never_refetched(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    evidence = "https://example.test/final"
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("read_webpage", evidence=(evidence,))]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )

    class ForbiddenNetworkClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ordinary HTTP evidence must not be fetched")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenNetworkClient)

    assert await reader.reference_exists(evidence, tenant_id, run_id) is True

    unlisted = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("read_webpage", evidence=(evidence,))]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    assert await unlisted.reference_exists("https://attacker.test/not-in-ledger", tenant_id, run_id) is False

    unsupported = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([_execution("unknown_http_tool", evidence=(evidence,))]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    assert await unsupported.reference_exists(evidence, tenant_id, run_id) is False

    missing_snapshot_execution = _execution("read_webpage", evidence=(evidence,))
    missing_snapshot_execution.result_summary = ""
    no_snapshot = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult([missing_snapshot_execution]),
        ),
        storage=_Storage(),  # type: ignore[arg-type]
    )
    assert await no_snapshot.reference_exists(evidence, tenant_id, run_id) is False


@pytest.mark.asyncio
async def test_publish_http_evidence_uses_db_source_not_network() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    stable_ref = "published-page://page-123"
    evidence = "https://pages.example/p/page-123"
    page = SimpleNamespace(
        short_id="page-123",
        agent_id=agent_id,
        tenant_id=tenant_id,
        source_path="workspace/page.html",
    )
    reader = RuntimeToolReferenceReader(
        session_factory=_factory(
            _ScalarResult(agent_id),
            _ManyResult(
                [
                    _execution(
                        "publish_page",
                        artifacts=(stable_ref,),
                        evidence=(evidence,),
                    )
                ]
            ),
            _ScalarResult(page),
        ),
        storage=_Storage({f"{agent_id}/workspace/page.html"}),  # type: ignore[arg-type]
    )

    assert await reader.reference_exists(evidence, tenant_id, run_id) is True


@pytest.mark.asyncio
async def test_production_verifier_and_finalizer_propagate_only_read_back_refs() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    reference = f"workspace://{agent_id}/workspace/report.pdf"
    execution = SimpleNamespace(
        status="succeeded",
        tool_call_id="call-1",
        tool_name="convert_html_to_pdf",
        result_ref=None,
        result_metadata={"artifact_refs": [reference], "evidence_refs": []},
    )
    session_factory = _factory(
        _ManyResult([execution]),
        _ScalarResult(agent_id),
        _ManyResult([execution]),
    )
    reader = RuntimeToolReferenceReader(
        session_factory=session_factory,
        storage=_Storage({f"{agent_id}/workspace/report.pdf"}),  # type: ignore[arg-type]
    )
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=session_factory,
        reference_exists=reader.reference_exists,
    )
    state = {"lifecycle": {"pending_tool_calls": []}}
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=object(),  # type: ignore[arg-type]
    )

    verified = await verifier.verify(state, context, "done")  # type: ignore[arg-type]
    assert verified.outcome == "pass"
    assert verified.details["artifact_refs"] == [reference]

    finalized = await DefaultRuntimeFinalizer().finalize(
        state,  # type: ignore[arg-type]
        context,
        "done",
        verified,
    )
    assert finalized.result_summary["artifact_refs"] == [reference]
    assert finalized.result_summary["evidence_refs"] == []


@pytest.mark.asyncio
async def test_production_verifier_repairs_an_unreadable_current_run_reference() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    reference = f"workspace://{agent_id}/workspace/missing.pdf"
    execution = SimpleNamespace(
        status="succeeded",
        tool_call_id="call-1",
        tool_name="convert_html_to_pdf",
        result_ref=None,
        result_metadata={"artifact_refs": [reference], "evidence_refs": []},
    )
    session_factory = _factory(
        _ManyResult([execution]),
        _ScalarResult(agent_id),
        _ManyResult([execution]),
    )
    reader = RuntimeToolReferenceReader(
        session_factory=session_factory,
        storage=_Storage(),  # type: ignore[arg-type]
    )
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=session_factory,
        reference_exists=reader.reference_exists,
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=object(),  # type: ignore[arg-type]
    )

    result = await verifier.verify(  # type: ignore[arg-type]
        {"lifecycle": {"pending_tool_calls": []}},
        context,
        "done",
    )
    assert result.outcome == "repair"
    assert result.details == {
        "code": "tool_reference_unreadable",
        "reference": reference,
    }
