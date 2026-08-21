"""Typed Runtime tool outcome, private result, and verifier contracts."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.models.agent_tool_execution import AgentToolExecution
from app.services import agent_tools
from app.models.llm import LLMModel
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    execution_outcome,
    normalize_tool_outcome,
    sanitize_tool_arguments,
)
from app.services.agent_runtime.tool_result_store import (
    ToolResultReconciler,
    ToolResultStore,
    ToolResultStoreError,
)
from app.services.agent_runtime.verification import (
    CompletionGateRuntimeVerifier,
    TaskCompletionGate,
    ToolLedgerRuntimeVerifier,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.storage_runtime.base import StorageBackend
from app.services.token_tracker import TokenUsage


class _MemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def read_bytes(self, key: str) -> bytes:
        try:
            return self.values[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def write_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        del content_type
        self.values[key] = data


class _FailingReadStorage(_MemoryStorage):
    async def read_bytes(self, key: str) -> bytes:
        del key
        raise TimeoutError("object storage probe timed out")


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Scalars:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return list(self.values)


class _ManyResult:
    def __init__(self, values) -> None:
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _DB:
    def __init__(self, *results) -> None:
        self.results = deque(results)

    async def execute(self, statement):
        del statement
        value = self.results.popleft()
        return value

    @asynccontextmanager
    async def begin(self):
        yield self

    async def flush(self) -> None:
        return None


class _FailingDB(_DB):
    async def execute(self, statement):
        del statement
        raise TimeoutError("ledger settlement timed out")


def _factory(*results):
    @asynccontextmanager
    async def factory():
        yield _DB(*results)

    return factory


def _sequence_factory(*databases: _DB):
    remaining = deque(databases)

    @asynccontextmanager
    async def factory():
        yield remaining.popleft()

    return factory


def _failing_factory():
    @asynccontextmanager
    async def factory():
        yield _FailingDB()

    return factory


def _execution(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str = "started",
) -> AgentToolExecution:
    return AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id="call-1",
        tool_name="read_file",
        assistant_message_id="assistant-1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect="read",
        retry_policy="safe",
        result_metadata={},
        status=status,
        lease_owner="worker-1",
    )


def _state(tenant_id: uuid.UUID, run_id: uuid.UUID) -> RuntimeGraphState:
    del tenant_id, run_id
    return {
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": "verifying",
            "next_route": "verify",
            "pending_tool_calls": [],
        },
    }


def _context(tenant_id: uuid.UUID, run_id: uuid.UUID) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id="command-1",
        executor=object(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_private_binary_resolves_after_store_restart_with_ledger_integrity() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    content = b"private-screenshot-bytes"
    storage = _MemoryStorage()
    writer = ToolResultStore(
        session_factory=_factory(),
        storage=storage,
    )

    receipt = await writer.write_binary(
        execution,
        content,
        mime_type="image/png",
    )
    execution.status = "succeeded"
    execution.result_metadata = {
        "evidence_refs": [receipt.ref],
        "content_hash": hashlib.sha256(content).hexdigest(),
        "mime_type": "image/png",
        "size": len(content),
    }

    restarted = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=storage,
    )
    assert await restarted.resolve_binary(
        receipt.ref,
        tenant_id=tenant_id,
        run_id=run_id,
    ) == content

    storage.values[writer.binary_storage_key(execution)] = b"tampered"
    tampered_reader = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=storage,
    )
    with pytest.raises(ToolResultStoreError) as exc_info:
        await tampered_reader.resolve_binary(
            receipt.ref,
            tenant_id=tenant_id,
            run_id=run_id,
        )
    assert exc_info.value.code == "tool_binary_integrity_mismatch"


def test_arguments_are_recursively_redacted_without_changing_the_raw_fingerprint_input() -> None:
    sanitized = sanitize_tool_arguments(
        {
            "nested": {"api_key": "secret-key", "safe": "visible"},
            "Authorization": "Bearer abc.def",
            "url": "https://example.test/path?token=secret&view=full",
            "signed": (
                "https://bucket.test/object?X-Amz-Signature=secret-signature"
                "&response-content-type=text/plain"
            ),
            "message": "postgresql://user:password@example.test/db",
            "code": 'SECRET = "credential-value"\nprint("done")',
            "bad\x00key": "normalized",
            "items": [{"cookie": "sid=secret"}],
        }
    )

    assert sanitized["nested"] == {"api_key": "[REDACTED]", "safe": "visible"}
    assert sanitized["Authorization"] == "[REDACTED]"
    assert "secret" not in sanitized["url"]
    assert "view=full" in sanitized["url"]
    assert "secret-signature" not in sanitized["signed"]
    assert "user:password" not in sanitized["message"]
    assert "credential-value" not in sanitized["code"]
    assert "SECRET = [REDACTED]" in sanitized["code"]
    assert sanitized["bad�key"] == "normalized"
    assert all("\x00" not in key for key in sanitized)
    assert sanitized["items"] == [{"cookie": "[REDACTED]"}]


def test_outcome_normalizer_replaces_controls_redacts_credentials_and_caps_utf8_bytes() -> None:
    raw = (
        "prefix\x00\x01\t\n\r Authorization: Bearer very-secret-token\n"
        + "界" * 100
    )
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary=raw,
            result_ref=None,
            artifact_refs=("artifact://safe\x00id",),
        ),
        effect="read",
        retry_policy="safe",
        inline_max_bytes=96,
    )

    assert archived_body is not None
    assert "\x00" not in archived_body
    assert "\x01" not in archived_body
    assert "\t\n\r" in archived_body
    assert "very-secret-token" not in archived_body
    assert len((normalized.result_summary or "").encode("utf-8")) <= 96
    assert normalized.artifact_refs == ("artifact://safe�id",)
    assert normalized.metadata["nul_replacements"] == 2
    assert normalized.metadata["control_replacements"] == 1
    assert normalized.metadata["redaction_count"] >= 1
    assert normalized.metadata["summary_truncated"] is True
    assert normalized.metadata["content_hash"]


def test_failure_feedback_fields_are_sanitized_bounded_and_replayable() -> None:
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="failed",
            result_summary="Argument validation failed.",
            result_ref=None,
            error_code="tool_arguments_invalid",
            model_action="repair_arguments",
            side_effect_state="none",
            safe_remediation=(
                "Correct $.path; Authorization: Bearer must-not-survive\x00"
                + "界" * 300
            ),
        ),
        effect="read",
        retry_policy="safe",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.model_action == "repair_arguments"
    assert normalized.side_effect_state == "none"
    assert normalized.safe_remediation is not None
    assert "must-not-survive" not in normalized.safe_remediation
    assert "\x00" not in normalized.safe_remediation
    assert len(normalized.safe_remediation.encode("utf-8")) <= 512
    assert normalized.metadata["model_action"] == "repair_arguments"
    assert normalized.metadata["side_effect_state"] == "none"
    assert normalized.metadata["safe_remediation"] == normalized.safe_remediation

    execution = _execution(
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        status="failed",
    )
    execution.result_summary = normalized.result_summary
    execution.result_ref = normalized.result_ref
    execution.result_metadata = normalized.metadata
    replayed = execution_outcome(execution)

    assert replayed.model_action == normalized.model_action
    assert replayed.side_effect_state == normalized.side_effect_state
    assert replayed.safe_remediation == normalized.safe_remediation


@pytest.mark.parametrize(
    ("error_code", "event_key"),
    (
        ("tool_deadline_outcome_unknown", "deadline_exceeded"),
        ("tool_cancelled_outcome_unknown", "cancel_requested"),
    ),
)
def test_possible_write_after_deadline_or_cancel_is_unknown_and_not_replayable(
    error_code: str,
    event_key: str,
) -> None:
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="unknown",
            result_summary="External write may have happened; reconcile first.",
            result_ref=None,
            error_code=error_code,
            retryable=False,
            model_action="reconcile",
            side_effect_state="unknown",
            metadata={event_key: True},
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.status == "unknown"
    assert normalized.retryable is False
    assert normalized.model_action == "reconcile"
    assert normalized.side_effect_state == "unknown"
    assert normalized.metadata[event_key] is True


def test_outcome_normalizer_preserves_bounded_email_provider_receipt() -> None:
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Email accepted for 1 recipient.",
            result_ref="<outbound-1@example.test>",
            metadata={
                "message_id": "<outbound-1@example.test>",
                "accepted_recipients": ["alice@example.test"],
                "refused_recipients": [],
                "provider_response": "must-not-persist",
            },
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.metadata["message_id"] == "<outbound-1@example.test>"
    assert normalized.metadata["accepted_recipients"] == [
        "alice@example.test"
    ]
    assert normalized.metadata["refused_recipients"] == []
    assert "provider_response" not in normalized.metadata


def test_outcome_normalizer_preserves_sanitized_feishu_provider_receipt() -> None:
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="failed",
            result_summary=(
                "Feishu rejected approval_create: HTTP 400; code 1390001."
            ),
            result_ref=None,
            error_code="feishu_approval_create_rejected",
            metadata={
                "provider_http_status": 400,
                "provider_code": 1390001,
                "provider_msg": "param is invalid",
                "provider_response_body": {
                    "code": 1390001,
                    "msg": "param is invalid",
                    "authorization": "must-not-persist",
                },
            },
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.metadata["provider_http_status"] == 400
    assert normalized.metadata["provider_code"] == 1390001
    assert normalized.metadata["provider_msg"] == "param is invalid"
    assert normalized.metadata["provider_response_body"] == {
        "code": 1390001,
        "msg": "param is invalid",
        "authorization": "[REDACTED]",
    }


def test_outcome_normalizer_preserves_bounded_okr_transaction_receipt() -> None:
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Updated KR with a durable progress receipt.",
            result_ref="kr-1",
            metadata={
                "kr_id": "kr-1",
                "progress_log_id": "log-1",
                "previous_value": 2.0,
                "current_value": 8.0,
                "target_value": 10.0,
                "status": "on_track",
                "content_truncated": False,
                "okr_content_hash": "abc123",
                "operation_id": "operation-1",
                "updated_count": 1,
                "skipped_count": 2,
                "error_count": 0,
                "updated_refs": ["okr-progress-log://log-1"],
                "report_type": "daily",
                "workspace_path": "workspace/reports/daily.md",
                "db_status": "succeeded",
                "projection_status": "succeeded",
                "provider_response": "must-not-persist",
            },
        ),
        effect="write",
        retry_policy="conditional",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.metadata["kr_id"] == "kr-1"
    assert normalized.metadata["progress_log_id"] == "log-1"
    assert normalized.metadata["previous_value"] == 2.0
    assert normalized.metadata["current_value"] == 8.0
    assert normalized.metadata["target_value"] == 10.0
    assert normalized.metadata["status"] == "on_track"
    assert normalized.metadata["content_truncated"] is False
    assert normalized.metadata["okr_content_hash"] == "abc123"
    assert normalized.metadata["operation_id"] == "operation-1"
    assert normalized.metadata["updated_count"] == 1
    assert normalized.metadata["skipped_count"] == 2
    assert normalized.metadata["error_count"] == 0
    assert normalized.metadata["updated_refs"] == [
        "okr-progress-log://log-1"
    ]
    assert normalized.metadata["report_type"] == "daily"
    assert normalized.metadata["workspace_path"] == (
        "workspace/reports/daily.md"
    )
    assert normalized.metadata["db_status"] == "succeeded"
    assert normalized.metadata["projection_status"] == "succeeded"
    assert "provider_response" not in normalized.metadata


def test_neon_private_value_ref_survives_normalizer_and_result_envelope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    value_ref = f"deploy-value://{tenant_id}/{uuid.uuid4()}/value-1"
    connection_uri = "postgresql://user:private@db.example/warehouse"
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Neon project created with a private value ref.",
            result_ref="project-1",
            evidence_refs=("neon-project://project-1",),
            metadata={
                "provider": "neon",
                "operation": "project_create",
                "project_id": "project-1",
                "database_name": "warehouse",
                "value_ref": value_ref,
                "provider_payload": connection_uri,
            },
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    assert normalized.metadata["value_ref"] == value_ref
    assert "provider_payload" not in normalized.metadata
    store = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=_MemoryStorage(),
    )
    envelope = store.build_envelope(execution, normalized, "bounded")
    serialized = json.dumps(envelope.to_json(), sort_keys=True)
    assert envelope.metadata["value_ref"] == value_ref
    assert connection_uri not in serialized


def test_vercel_deploy_receipts_survive_normalizer_and_result_envelope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    receipt_metadata = {
        "provider": "vercel",
        "operation": "deployment_accepted",
        "project_id": "project-1",
        "project_name": "app",
        "deploy_method": "upload",
        "git_ref": "main",
        "linked_repo": "owner/repo",
        "confirmed_blob_digests": ["a" * 40, "b" * 40],
        "deployment_id": "deployment-1",
        "deployment_url": "https://app-abc.vercel.app",
        "deployment_state": "READY",
        "provider_payload": "must-not-persist",
    }
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Vercel deployment deployment-1 is READY.",
            result_ref="deployment-1",
            artifact_refs=("https://app-abc.vercel.app",),
            evidence_refs=("vercel-deployment://deployment-1",),
            metadata=receipt_metadata,
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    for key, value in receipt_metadata.items():
        if key != "provider_payload":
            assert normalized.metadata[key] == value
    assert normalized.metadata["artifact_refs"] == [
        "https://app-abc.vercel.app"
    ]
    assert normalized.metadata["evidence_refs"] == [
        "vercel-deployment://deployment-1"
    ]
    assert "provider_payload" not in normalized.metadata
    store = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=_MemoryStorage(),
    )
    envelope = store.build_envelope(execution, normalized, "bounded")
    serialized = json.dumps(envelope.to_json(), sort_keys=True)
    for key, value in receipt_metadata.items():
        if key != "provider_payload":
            assert envelope.metadata[key] == value
    assert "provider_payload" not in serialized


def test_image_workspace_receipt_survives_normalizer_and_result_envelope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    workspace_ref = "workspace://agent-1/workspace/images/result.png"
    receipt_metadata = {
        "provider": "openai",
        "operation": "image_generation",
        "workspace_path": "workspace/images/result.png",
        "content_hash": "a" * 64,
        "artifact_content_hash": "a" * 64,
        "mime_type": "image/png",
        "size": 68,
        "provider_payload": "must-not-persist",
    }
    normalized, archived_body = normalize_tool_outcome(
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="Generated image saved to the workspace.",
            result_ref=workspace_ref,
            artifact_refs=(workspace_ref,),
            metadata=receipt_metadata,
        ),
        effect="external_write",
        retry_policy="never",
        inline_max_bytes=1024,
    )

    assert archived_body is None
    for key, value in receipt_metadata.items():
        if key not in {"content_hash", "provider_payload"}:
            assert normalized.metadata[key] == value
    assert normalized.metadata["content_hash"] != receipt_metadata["content_hash"]
    assert "provider_payload" not in normalized.metadata
    store = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=_MemoryStorage(),
    )
    envelope = store.build_envelope(execution, normalized, "bounded")
    serialized = json.dumps(envelope.to_json(), sort_keys=True)
    for key, value in receipt_metadata.items():
        if key not in {"content_hash", "provider_payload"}:
            assert envelope.metadata[key] == value
    assert workspace_ref in envelope.artifact_refs
    assert "provider_payload" not in serialized


@pytest.mark.asyncio
async def test_deploy_value_store_encrypts_and_enforces_agent_scope(
    monkeypatch,
) -> None:
    storage = _MemoryStorage()
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    secret = "postgresql://user:private@db.example/warehouse"

    async def tenant_for_agent(_agent_id):
        return str(tenant_id)

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_for_agent)
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    value_ref = await agent_tools._store_deploy_value_ref(agent_id, secret)

    assert value_ref.startswith(f"deploy-value://{tenant_id}/{agent_id}/")
    assert len(storage.values) == 1
    storage_key, encrypted = next(iter(storage.values.items()))
    assert storage_key.startswith(
        f"runtime/deploy-values/{tenant_id}/{agent_id}/"
    )
    assert secret.encode() not in encrypted
    assert await agent_tools._resolve_deploy_value_ref(agent_id, value_ref) == secret
    with pytest.raises(PermissionError, match="scope"):
        await agent_tools._resolve_deploy_value_ref(uuid.uuid4(), value_ref)


@pytest.mark.asyncio
async def test_private_result_store_uses_deterministic_key_and_checks_ledger_scope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    storage = _MemoryStorage()
    store = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=storage,
    )
    outcome = ToolExecutionOutcome(
        status="succeeded",
        result_summary="bounded",
        result_ref=None,
        artifact_refs=("artifact://one",),
        evidence_refs=("evidence://one",),
        metadata={"content_hash": "ignored-and-recomputed"},
    )

    result_ref = await store.write(execution, outcome, "full normalized result")
    expected_key = (
        f"runtime/tool-results/{tenant_id}/{run_id}/{execution.id}.json"
    )
    assert result_ref == f"tool-result://{execution.id}"
    assert set(storage.values) == {expected_key}

    execution.status = "succeeded"
    execution.result_ref = result_ref
    envelope = await store.resolve(
        result_ref,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    assert envelope.content == "full normalized result"
    assert envelope.execution_id == execution.id
    assert envelope.artifact_refs == ("artifact://one",)

    with pytest.raises(Exception, match="tenant|scope"):
        await store.resolve(
            result_ref,
            tenant_id=uuid.uuid4(),
            run_id=run_id,
        )


@pytest.mark.asyncio
async def test_result_reconciler_settles_an_expired_started_receipt_from_envelope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    storage = _MemoryStorage()
    store = ToolResultStore(
        session_factory=_factory(),
        storage=storage,
    )
    outcome = ToolExecutionOutcome(
        status="succeeded",
        result_summary="bounded result",
        result_ref=None,
        artifact_refs=("artifact://one",),
        evidence_refs=("evidence://one",),
        metadata={"content_hash": "normalizer-hash"},
    )
    await store.write(execution, outcome, "full normalized result")
    reconciler = ToolResultReconciler(
        session_factory=_sequence_factory(
            _DB(_ManyResult([execution])),
            _DB(_ScalarResult(execution), _ScalarResult(execution)),
        ),
        result_store=store,
    )

    result = await reconciler.run_once()

    assert result.status == "reconciled"
    assert result.execution_id == execution.id
    assert execution.status == "succeeded"
    assert execution.result_ref == f"tool-result://{execution.id}"
    assert execution.result_metadata["archive_status"] == "stored"
    assert execution.result_metadata["artifact_refs"] == ["artifact://one"]


@pytest.mark.asyncio
async def test_result_reconciler_does_not_guess_success_without_an_envelope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    store = ToolResultStore(
        session_factory=_factory(),
        storage=_MemoryStorage(),
    )
    reconciler = ToolResultReconciler(
        session_factory=_sequence_factory(_DB(_ManyResult([execution]))),
        result_store=store,
    )

    result = await reconciler.run_once()

    assert result.status == "deferred"
    assert execution.status == "started"
    assert execution.result_ref is None


@pytest.mark.asyncio
async def test_result_reconciler_defers_transient_storage_probe_failures() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    reconciler = ToolResultReconciler(
        session_factory=_factory(),
        result_store=ToolResultStore(
            session_factory=_factory(),
            storage=_FailingReadStorage(),
        ),
    )

    result = await reconciler.reconcile_candidate(execution)

    assert result.status == "deferred"
    assert result.error_code == "tool_result_probe_failed"
    assert execution.status == "started"
    assert execution.result_ref is None


@pytest.mark.asyncio
async def test_result_reconciler_defers_transient_ledger_settlement_failures() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    storage = _MemoryStorage()
    store = ToolResultStore(
        session_factory=_failing_factory(),
        storage=storage,
    )
    await store.write(
        execution,
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="archived result",
            result_ref=None,
        ),
        "full archived result",
    )
    reconciler = ToolResultReconciler(
        session_factory=_failing_factory(),
        result_store=store,
    )

    result = await reconciler.reconcile_candidate(execution)

    assert result.status == "deferred"
    assert result.error_code == "tool_result_settlement_failed"
    assert execution.status == "started"
    assert execution.result_ref is None


@pytest.mark.asyncio
async def test_result_reconciler_rechecks_lease_before_settlement() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    execution.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    storage = _MemoryStorage()
    store = ToolResultStore(
        session_factory=_factory(),
        storage=storage,
    )
    await store.write(
        execution,
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="bounded result",
            result_ref=None,
        ),
        "full normalized result",
    )
    reconciler = ToolResultReconciler(
        session_factory=_sequence_factory(
            _DB(_ManyResult([execution])),
            _DB(_ScalarResult(execution)),
        ),
        result_store=store,
    )

    result = await reconciler.run_once()

    assert result.status == "deferred"
    assert execution.status == "started"
    assert execution.result_ref is None


@pytest.mark.asyncio
async def test_verifier_blocks_unsettled_facts_and_collects_only_succeeded_refs() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    started = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=_factory(_ManyResult([started])),
    )

    blocked = await verifier.verify(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
        "done",
    )

    assert blocked.outcome == "fail"
    assert blocked.details["code"] == "unsettled_tool_execution"
    assert blocked.details["tool_call_ids"] == ["call-1"]

    succeeded = _execution(tenant_id=tenant_id, run_id=run_id, status="succeeded")
    succeeded.result_metadata = {
        "artifact_refs": ["artifact://one"],
        "evidence_refs": ["evidence://one"],
    }
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=_factory(_ManyResult([succeeded])),
        reference_exists=lambda ref, tenant, run: _true_reference(
            ref,
            tenant,
            run,
        ),
    )

    passed = await verifier.verify(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
        "done",
    )

    assert passed.outcome == "pass"
    assert passed.details["artifact_refs"] == ["artifact://one"]
    assert passed.details["evidence_refs"] == ["evidence://one"]


@pytest.mark.asyncio
async def test_verifier_repairs_declared_async_pending_with_exact_poll_action() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pending = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    pending.result_metadata = {
        "runtime_async_pending": True,
        "async_operation": {
            "version": 1,
            "operation_key": "operation-key",
            "operation_id": "2501.01234",
            "state": "downloading",
            "poll": {
                "tool": "arxiv_local-download_paper",
                "arguments": {"paper_id": "2501.01234", "check_status": True},
                "interval_ms": 1000,
            },
        },
    }
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=_factory(_ManyResult([pending])),
    )

    blocked = await verifier.verify(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
        "done",
    )

    assert blocked.outcome == "repair"
    assert blocked.details["code"] == "async_tool_pending"
    assert blocked.details["operations"][0]["poll"]["tool"] == (
        "arxiv_local-download_paper"
    )
    assert "check_status" in (blocked.reason or "")


@pytest.mark.asyncio
async def test_verifier_uses_invocation_context_without_checkpoint_registry() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    verifier = ToolLedgerRuntimeVerifier(
        session_factory=_factory(_ManyResult([])),
    )

    passed = await verifier.verify(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
        "done",
    )

    assert passed.outcome == "pass"
    assert passed.details["code"] == "deterministic_checks_passed"


@pytest.mark.asyncio
async def test_completion_gate_invalid_output_fails_open() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        tenant_id=tenant_id,
        provider="openai",
        model="judge-model",
        api_key_encrypted="unused",
        label="Judge",
        enabled=True,
    )

    async def invalid_completion(*args, **kwargs):
        del args, kwargs
        return LLMCompletionStep(
            content="not json",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    gate = TaskCompletionGate(
        session_factory=_factory(_ScalarResult(model)),
        completion=invalid_completion,
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id="command-gate",
        executor=object(),  # type: ignore[arg-type]
        goal="Produce the requested report",
        model_id=str(model_id),
        agent_id=str(agent_id),
    )

    result = await gate.verify(_state(tenant_id, run_id), context, "report result")

    assert result.outcome == "pass"
    assert result.details == {
        "code": "completion_gate_error",
        "gate_error_code": "invalid_completion_gate_output",
    }


@pytest.mark.asyncio
async def test_completion_gate_explicit_repair_is_actionable() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        tenant_id=tenant_id,
        provider="openai",
        model="judge-model",
        api_key_encrypted="unused",
        label="Judge",
        enabled=True,
    )

    async def repair_completion(*args, **kwargs):
        del args, kwargs
        return LLMCompletionStep(
            content=json.dumps(
                {
                    "verdict": "repair",
                    "missing_requirements": ["The report file was not read back"],
                    "next_actions": ["Read the report and verify its contents"],
                    "evidence": ["write_file succeeded"],
                }
            ),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    gate = TaskCompletionGate(
        session_factory=_factory(_ScalarResult(model)),
        completion=repair_completion,
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id="command-gate",
        executor=object(),  # type: ignore[arg-type]
        goal="Produce and verify the requested report",
        model_id=str(model_id),
        agent_id=str(agent_id),
    )

    result = await gate.verify(_state(tenant_id, run_id), context, "report done")

    assert result.outcome == "repair"
    assert result.details["code"] == "task_completion_repair_required"
    assert "Read the report" in (result.reason or "")


@pytest.mark.asyncio
async def test_completion_gate_treats_workspace_preservation_as_task_amendment() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        tenant_id=tenant_id,
        provider="openai",
        model="judge-model",
        api_key_encrypted="unused",
        label="Judge",
        enabled=True,
    )
    captured: dict[str, object] = {}

    async def passing_completion(_model, messages, **_kwargs):
        captured["system"] = messages[0].content
        captured["payload"] = json.loads(messages[1].content)
        return LLMCompletionStep(
            content=json.dumps(
                {
                    "verdict": "pass",
                    "missing_requirements": [],
                    "next_actions": [],
                    "evidence": ["human Workspace decision"],
                }
            ),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    gate = TaskCompletionGate(
        session_factory=_factory(_ScalarResult(model)),
        completion=passing_completion,
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id="command-gate",
        executor=object(),  # type: ignore[arg-type]
        goal="Write AGENT candidate bytes",
        model_id=str(model_id),
        agent_id=str(agent_id),
    )
    state = _state(tenant_id, run_id)
    state["messages"] = [
        {
            "id": "resume-workspace",
            "role": "user",
            "content": "保留工作区中的源文件，不要覆盖。",
            "runtime_input": "resume",
            "runtime_confirmation_text": "keep_workspace",
            "runtime_reconciliation_action": "keep_workspace",
        }
    ]

    result = await gate.verify(state, context, "已保留源文件并继续。")

    assert result.outcome == "pass"
    assert "latest human decision wins" in str(captured["system"])
    payload = captured["payload"]
    assert isinstance(payload, dict)
    amendments = payload["available_evidence"]["authoritative_task_amendments"]
    assert amendments == [
        {
            "content": "保留工作区中的源文件，不要覆盖。",
            "runtime_confirmation_text": "keep_workspace",
            "runtime_reconciliation_action": "keep_workspace",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "decision", "expected_outcome"),
    (
        (
            "我还缺少会议日期。请问安排在哪一天？",
            {
                "verdict": "pass",
                "missing_requirements": [],
                "next_actions": [],
                "evidence": ["one concrete public clarification"],
            },
            "pass",
        ),
        (
            "日程已经创建，请告诉我会议日期。",
            {
                "verdict": "repair",
                "missing_requirements": ["No event receipt proves creation"],
                "next_actions": ["Do not claim the deferred write completed"],
                "evidence": [],
            },
            "repair",
        ),
    ),
)
async def test_completion_gate_public_group_clarification_contract_is_bounded(
    candidate,
    decision,
    expected_outcome,
) -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    model_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        tenant_id=tenant_id,
        provider="openai",
        model="judge-model",
        api_key_encrypted="unused",
        label="Judge",
        enabled=True,
    )
    captured: dict[str, object] = {}

    async def completion(_model, messages, **_kwargs):
        captured["system"] = messages[0].content
        captured["payload"] = json.loads(messages[1].content)
        return LLMCompletionStep(
            content=json.dumps(decision),
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(),
        )

    gate = TaskCompletionGate(
        session_factory=_factory(_ScalarResult(model)),
        completion=completion,
    )
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id="command-group-gate",
        executor=object(),  # type: ignore[arg-type]
        goal="Create a calendar event after receiving the missing date",
        model_id=str(model_id),
        agent_id=str(agent_id),
    )
    state = _state(tenant_id, run_id)
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={
            "chat_session_type": "group",
            "source_channel": "feishu",
        },
    )

    result = await gate.verify(state, context, candidate)

    assert result.outcome == expected_outcome
    assert "asks one concrete" in str(captured["system"])
    assert "does not permit bypassing confirmation" in str(captured["system"])
    assert "side effects or treating an unsettled Tool outcome" in str(
        captured["system"]
    )
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["candidate_final_answer"] == candidate
    assert payload["available_evidence"]["initial_input"]["chat_session_type"] == (
        "group"
    )


@pytest.mark.asyncio
async def test_completion_gate_never_bypasses_unsettled_public_group_tool() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    started = _execution(tenant_id=tenant_id, run_id=run_id, status="started")
    deterministic = ToolLedgerRuntimeVerifier(
        session_factory=_factory(_ManyResult([started])),
    )

    class _NeverCalledCompletionGate:
        async def verify(self, *_args, **_kwargs):
            raise AssertionError("semantic completion gate must not run")

    verifier = CompletionGateRuntimeVerifier(
        deterministic=deterministic,
        completion_gate=_NeverCalledCompletionGate(),  # type: ignore[arg-type]
    )
    state = _state(tenant_id, run_id)
    state["snapshots"] = RunInputSnapshots(
        session_context={"version": 0},
        session_context_version=0,
        recent_session_messages=(),
        related_run_summaries=(),
        initial_input={"chat_session_type": "group", "source_channel": "feishu"},
    )

    result = await verifier.verify(
        state,
        _context(tenant_id, run_id),
        "请确认是否继续。",
    )

    assert result.outcome == "fail"
    assert result.details["code"] == "unsettled_tool_execution"


async def _true_reference(
    ref: str,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> bool:
    return bool(ref and tenant_id and run_id)


def test_result_store_envelope_does_not_leak_storage_key_or_unknown_metadata() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    execution = _execution(tenant_id=tenant_id, run_id=run_id)
    storage = _MemoryStorage()
    store = ToolResultStore(
        session_factory=_factory(_ScalarResult(execution)),
        storage=storage,
    )

    envelope = store.build_envelope(
        execution,
        ToolExecutionOutcome(
            status="succeeded",
            result_summary="ok",
            result_ref=None,
            metadata={"provider_payload": "must-not-persist"},
        ),
        "body",
    )

    serialized = json.dumps(envelope.to_json(), sort_keys=True)
    assert "runtime/tool-results" not in serialized
    assert "provider_payload" not in serialized
