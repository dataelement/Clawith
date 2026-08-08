"""Private deterministic object storage for oversized Runtime tool results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import Any, Literal
import uuid

from sqlalchemy import select

from app.models.agent_tool_execution import AgentToolExecution
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    execution_outcome,
    mark_tool_execution_succeeded,
)
from app.services.storage_runtime.base import StorageBackend
from app.services.storage_runtime.facade import get_storage_backend


_ENVELOPE_VERSION = 1
_BINARY_REF_PREFIX = "tool-result-binary://"
logger = logging.getLogger(__name__)
_METADATA_KEYS = frozenset(
    {
        "error_code",
        "error_class",
        "retryable",
        "artifact_refs",
        "evidence_refs",
        "nul_replacements",
        "control_replacements",
        "redaction_count",
        "summary_truncated",
        "content_hash",
        "artifact_content_hash",
        "mime_type",
        "size",
        "workspace_path",
        "archive_status",
        "archive_error_code",
        "provider",
        "operation",
        "project_id",
        "project_name",
        "database_name",
        "region",
        "value_ref",
        "env_id",
        "env_key",
        "targets",
        "domain",
        "verified",
        "available",
        "price",
        "period",
        "deploy_method",
        "git_ref",
        "linked_repo",
        "confirmed_blob_digests",
        "deployment_id",
        "deployment_url",
        "deployment_state",
    }
)


class ToolResultStoreError(RuntimeError):
    """An opaque result reference failed identity or integrity validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolResultEnvelope:
    version: int
    execution_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    tool_call_id: str
    status: str
    summary: str | None
    artifact_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    metadata: dict[str, Any]
    content_hash: str
    content: str

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "execution_id": str(self.execution_id),
            "tenant_id": str(self.tenant_id),
            "run_id": str(self.run_id),
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "summary": self.summary,
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
            "content_hash": self.content_hash,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class ToolResultReconcileResult:
    """Outcome of one bounded private-result reconciliation pass."""

    status: Literal["idle", "reconciled", "unavailable", "deferred"]
    execution_id: uuid.UUID | None = None
    outcome: ToolExecutionOutcome | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ToolBinaryReceipt:
    """Opaque receipt for one execution-scoped private binary object."""

    ref: str
    content_hash: str
    mime_type: str
    size: int


def _execution_id(result_ref: str) -> uuid.UUID:
    prefix = "tool-result://"
    if not isinstance(result_ref, str) or not result_ref.startswith(prefix):
        raise ToolResultStoreError(
            "invalid_tool_result_ref",
            "tool result ref must use the tool-result scheme",
        )
    try:
        return uuid.UUID(result_ref[len(prefix) :])
    except ValueError as exc:
        raise ToolResultStoreError(
            "invalid_tool_result_ref",
            "tool result ref has an invalid execution identity",
        ) from exc


def _binary_execution_id(result_ref: str) -> uuid.UUID:
    if not isinstance(result_ref, str) or not result_ref.startswith(
        _BINARY_REF_PREFIX
    ):
        raise ToolResultStoreError(
            "invalid_tool_binary_ref",
            "private binary ref has an invalid scheme",
        )
    try:
        return uuid.UUID(result_ref[len(_BINARY_REF_PREFIX) :])
    except ValueError as exc:
        raise ToolResultStoreError(
            "invalid_tool_binary_ref",
            "private binary ref has an invalid execution identity",
        ) from exc


def _json_metadata(value: dict[str, Any]) -> dict[str, Any]:
    filtered = {key: item for key, item in value.items() if key in _METADATA_KEYS}
    try:
        encoded = json.dumps(
            filtered,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ToolResultStoreError(
            "invalid_tool_result_envelope",
            "tool result metadata is not finite JSON",
        ) from exc
    if not isinstance(copied, dict):  # pragma: no cover - constructed from dict
        raise ToolResultStoreError(
            "invalid_tool_result_envelope",
            "tool result metadata must be an object",
        )
    return copied


class ToolResultStore:
    """Write and resolve opaque refs outside Agent-visible storage namespaces."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        storage: StorageBackend | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage or get_storage_backend()

    @staticmethod
    def result_ref(execution_id: uuid.UUID) -> str:
        return f"tool-result://{execution_id}"

    @staticmethod
    def storage_key(execution: AgentToolExecution) -> str:
        return (
            "runtime/tool-results/"
            f"{execution.tenant_id}/{execution.run_id}/{execution.id}.json"
        )

    @staticmethod
    def binary_ref(execution_id: uuid.UUID) -> str:
        return f"{_BINARY_REF_PREFIX}{execution_id}"

    @staticmethod
    def binary_storage_key(execution: AgentToolExecution) -> str:
        return (
            "runtime/tool-results/"
            f"{execution.tenant_id}/{execution.run_id}/{execution.id}.bin"
        )

    def build_envelope(
        self,
        execution: AgentToolExecution,
        outcome: ToolExecutionOutcome,
        content: str,
    ) -> ToolResultEnvelope:
        return ToolResultEnvelope(
            version=_ENVELOPE_VERSION,
            execution_id=execution.id,
            tenant_id=execution.tenant_id,
            run_id=execution.run_id,
            tool_call_id=execution.tool_call_id,
            status=outcome.status,
            summary=outcome.result_summary,
            artifact_refs=tuple(outcome.artifact_refs),
            evidence_refs=tuple(outcome.evidence_refs),
            metadata=_json_metadata(outcome.metadata),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )

    async def write(
        self,
        execution: AgentToolExecution,
        outcome: ToolExecutionOutcome,
        content: str,
    ) -> str:
        """Write the deterministic envelope before the ledger is settled."""
        envelope = self.build_envelope(execution, outcome, content)
        encoded = json.dumps(
            envelope.to_json(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        await self._storage.write_bytes(
            self.storage_key(execution),
            encoded,
            content_type="application/json",
        )
        return self.result_ref(execution.id)

    async def write_binary(
        self,
        execution: AgentToolExecution,
        content: bytes,
        *,
        mime_type: str,
    ) -> ToolBinaryReceipt:
        """Archive bytes privately before the execution ledger is settled."""
        if not isinstance(content, bytes) or not content:
            raise ToolResultStoreError(
                "invalid_tool_binary_content",
                "private binary content must be non-empty bytes",
            )
        if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
            raise ToolResultStoreError(
                "invalid_tool_binary_mime_type",
                "private binary content requires an image MIME type",
            )
        await self._storage.write_bytes(
            self.binary_storage_key(execution),
            content,
            content_type=mime_type,
        )
        return ToolBinaryReceipt(
            ref=self.binary_ref(execution.id),
            content_hash=hashlib.sha256(content).hexdigest(),
            mime_type=mime_type,
            size=len(content),
        )

    async def resolve_binary(
        self,
        result_ref: str,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
    ) -> bytes:
        """Resolve bytes only after the settled ledger proves exact ownership."""
        execution_id = _binary_execution_id(result_ref)
        statement = select(AgentToolExecution).where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.id == execution_id,
        )
        if run_id is not None:
            statement = statement.where(AgentToolExecution.run_id == run_id)
        async with self._session_factory() as db:
            result = await db.execute(statement)
            execution = result.scalar_one_or_none()
        if execution is None or execution.status != "succeeded":
            raise ToolResultStoreError(
                "tool_binary_scope_mismatch",
                "private binary ref is not settled in this scope",
            )
        metadata = execution.result_metadata or {}
        refs = metadata.get("evidence_refs", [])
        if not isinstance(refs, list) or result_ref not in refs:
            raise ToolResultStoreError(
                "tool_binary_scope_mismatch",
                "private binary ref is not recorded by this execution",
            )
        try:
            content = await self._storage.read_bytes(
                self.binary_storage_key(execution)
            )
        except Exception as exc:
            raise ToolResultStoreError(
                "tool_binary_unavailable",
                "private binary content is unavailable",
            ) from exc
        expected_hash = metadata.get("content_hash")
        expected_size = metadata.get("size")
        if (
            not isinstance(expected_hash, str)
            or hashlib.sha256(content).hexdigest() != expected_hash
            or not isinstance(expected_size, int)
            or len(content) != expected_size
        ):
            raise ToolResultStoreError(
                "tool_binary_integrity_mismatch",
                "private binary content failed integrity validation",
            )
        return content

    async def resolve(
        self,
        result_ref: str,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
    ) -> ToolResultEnvelope:
        """Resolve only after the ledger proves tenant/run ownership and success."""
        execution_id = _execution_id(result_ref)
        statement = select(AgentToolExecution).where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.id == execution_id,
        )
        if run_id is not None:
            statement = statement.where(AgentToolExecution.run_id == run_id)
        async with self._session_factory() as db:
            result = await db.execute(statement)
            execution = result.scalar_one_or_none()
        if (
            execution is None
            or execution.tenant_id != tenant_id
            or run_id is not None
            and execution.run_id != run_id
        ):
            raise ToolResultStoreError(
                "tool_result_scope_mismatch",
                "tool result does not belong to the requested tenant/run scope",
            )
        if execution.status != "succeeded" or execution.result_ref != result_ref:
            raise ToolResultStoreError(
                "tool_result_not_settled",
                "tool result ledger fact is not a settled success",
            )
        envelope = await self._load_execution_envelope(execution)
        if envelope.status != execution.status:
            raise ToolResultStoreError(
                "tool_result_scope_mismatch",
                "tool result envelope status does not match its ledger fact",
            )
        return envelope

    async def load_for_reconciliation(
        self,
        execution: AgentToolExecution,
    ) -> ToolResultEnvelope:
        """Read a deterministic envelope without treating it as settled yet."""
        if execution.status != "started":
            raise ToolResultStoreError(
                "tool_result_not_reconcilable",
                "only a started tool receipt can be reconciled from an envelope",
            )
        envelope = await self._load_execution_envelope(execution)
        if envelope.status != "succeeded":
            raise ToolResultStoreError(
                "tool_result_not_reconcilable",
                "only a succeeded typed envelope can settle a started receipt",
            )
        return envelope

    async def _load_execution_envelope(
        self,
        execution: AgentToolExecution,
    ) -> ToolResultEnvelope:
        try:
            raw = await self._storage.read_bytes(self.storage_key(execution))
            payload = json.loads(raw)
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolResultStoreError(
                "tool_result_unreadable",
                "tool result envelope is missing or unreadable",
            ) from exc
        envelope = self._parse_envelope(payload)
        if (
            envelope.execution_id != execution.id
            or envelope.tenant_id != execution.tenant_id
            or envelope.run_id != execution.run_id
            or envelope.tool_call_id != execution.tool_call_id
        ):
            raise ToolResultStoreError(
                "tool_result_scope_mismatch",
                "tool result envelope identity does not match its ledger fact",
            )
        if hashlib.sha256(envelope.content.encode("utf-8")).hexdigest() != envelope.content_hash:
            raise ToolResultStoreError(
                "tool_result_integrity_failed",
                "tool result envelope content hash does not match",
            )
        return envelope

    @staticmethod
    def _parse_envelope(payload: object) -> ToolResultEnvelope:
        if not isinstance(payload, dict) or payload.get("version") != _ENVELOPE_VERSION:
            raise ToolResultStoreError(
                "invalid_tool_result_envelope",
                "tool result envelope version is invalid",
            )
        try:
            artifact_refs = payload.get("artifact_refs", [])
            evidence_refs = payload.get("evidence_refs", [])
            metadata = payload.get("metadata", {})
            if (
                not isinstance(artifact_refs, list)
                or not all(isinstance(value, str) for value in artifact_refs)
                or not isinstance(evidence_refs, list)
                or not all(isinstance(value, str) for value in evidence_refs)
                or not isinstance(metadata, dict)
                or not isinstance(payload["tool_call_id"], str)
                or not isinstance(payload["status"], str)
                or payload.get("summary") is not None
                and not isinstance(payload.get("summary"), str)
                or not isinstance(payload["content_hash"], str)
                or not isinstance(payload["content"], str)
            ):
                raise (TypeError("invalid envelope fields"))
            return ToolResultEnvelope(
                version=_ENVELOPE_VERSION,
                execution_id=uuid.UUID(str(payload["execution_id"])),
                tenant_id=uuid.UUID(str(payload["tenant_id"])),
                run_id=uuid.UUID(str(payload["run_id"])),
                tool_call_id=payload["tool_call_id"],
                status=payload["status"],
                summary=payload.get("summary"),
                artifact_refs=tuple(artifact_refs),
                evidence_refs=tuple(evidence_refs),
                metadata=_json_metadata(metadata),
                content_hash=payload["content_hash"],
                content=payload["content"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolResultStoreError(
                "invalid_tool_result_envelope",
                "tool result envelope fields are invalid",
            ) from exc


class ToolResultReconciler:
    """Settle expired started receipts only when their envelope proves success."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        result_store: ToolResultStore,
        batch_size: int = 32,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("tool result reconciliation batch_size must be positive")
        self._session_factory = session_factory
        self._result_store = result_store
        self._batch_size = batch_size

    async def run_once(self) -> ToolResultReconcileResult:
        """Reconcile at most one receipt without ever executing its tool again."""
        now = datetime.now(UTC)
        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentToolExecution)
                .where(
                    AgentToolExecution.status == "started",
                    AgentToolExecution.lease_expires_at.is_not(None),
                    AgentToolExecution.lease_expires_at <= now,
                )
                .order_by(
                    AgentToolExecution.started_at.asc(),
                    AgentToolExecution.id.asc(),
                )
                .limit(self._batch_size)
            )
            candidates = list(result.scalars().all())
        if not candidates:
            return ToolResultReconcileResult(status="idle")

        for candidate in candidates:
            reconciled = await self.reconcile_candidate(candidate)
            if reconciled.status == "unavailable":
                if reconciled.error_code != "tool_result_unreadable":
                    logger.warning(
                        "Tool result envelope could not reconcile execution %s: %s",
                        candidate.id,
                        reconciled.error_code,
                    )
                continue
            if reconciled.status == "reconciled":
                return reconciled
        return ToolResultReconcileResult(status="deferred")

    async def reconcile_candidate(
        self,
        candidate: AgentToolExecution,
    ) -> ToolResultReconcileResult:
        """Probe and settle one exact receipt without executing its provider."""
        try:
            envelope = await self._result_store.load_for_reconciliation(candidate)
        except ToolResultStoreError as exc:
            return ToolResultReconcileResult(
                status="unavailable",
                execution_id=candidate.id,
                error_code=exc.code,
            )
        except Exception as exc:
            logger.warning(
                "Tool result storage probe deferred execution %s: %s",
                candidate.id,
                type(exc).__name__,
            )
            return ToolResultReconcileResult(
                status="deferred",
                execution_id=candidate.id,
                error_code="tool_result_probe_failed",
            )
        try:
            outcome = await self._settle_if_still_expired(
                execution_id=candidate.id,
                envelope=envelope,
                now=datetime.now(UTC),
            )
        except Exception as exc:
            logger.warning(
                "Tool result ledger settlement deferred execution %s: %s",
                candidate.id,
                type(exc).__name__,
            )
            return ToolResultReconcileResult(
                status="deferred",
                execution_id=candidate.id,
                error_code="tool_result_settlement_failed",
            )
        if outcome is None:
            return ToolResultReconcileResult(
                status="deferred",
                execution_id=candidate.id,
            )
        return ToolResultReconcileResult(
            status="reconciled",
            execution_id=candidate.id,
            outcome=outcome,
        )

    async def _settle_if_still_expired(
        self,
        *,
        execution_id: uuid.UUID,
        envelope: ToolResultEnvelope,
        now: datetime,
    ) -> ToolExecutionOutcome | None:
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentToolExecution)
                    .where(AgentToolExecution.id == execution_id)
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None or execution.status != "started":
                    return None
                if (
                    execution.lease_expires_at is None
                    or execution.lease_expires_at > now
                    or not execution.lease_owner
                ):
                    return None
                if (
                    execution.id != envelope.execution_id
                    or execution.tenant_id != envelope.tenant_id
                    or execution.run_id != envelope.run_id
                    or execution.tool_call_id != envelope.tool_call_id
                ):
                    return None
                await mark_tool_execution_succeeded(
                    db,
                    tenant_id=execution.tenant_id,
                    execution_id=execution.id,
                    lease_owner=execution.lease_owner,
                    result_summary=envelope.summary,
                    result_ref=ToolResultStore.result_ref(execution.id),
                    error_code=(
                        str(envelope.metadata["error_code"])
                        if isinstance(envelope.metadata.get("error_code"), str)
                        else None
                    ),
                    retryable=envelope.metadata.get("retryable") is True,
                    artifact_refs=envelope.artifact_refs,
                    evidence_refs=envelope.evidence_refs,
                    metadata={
                        **envelope.metadata,
                        "content_hash": envelope.content_hash,
                        "archive_status": "stored",
                    },
                    clock=lambda: now,
                )
                return execution_outcome(execution)


__all__ = [
    "ToolResultEnvelope",
    "ToolResultReconcileResult",
    "ToolResultReconciler",
    "ToolResultStore",
    "ToolResultStoreError",
]
