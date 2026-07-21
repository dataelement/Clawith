"""Database-backed deterministic completion checks for Durable Runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
from urllib.parse import quote, unquote, urlsplit
import uuid

from sqlalchemy import select

from app.models.agent import Agent as AgentModel
from app.models.agent_run import AgentRun
from app.models.agent_tool_execution import AgentToolExecution
from app.models.published_page import PublishedPage
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.node_executor import VerificationResult
from app.services.agent_runtime.state import RuntimeContext, RuntimeGraphState
from app.services.agent_runtime.tool_result_store import (
    ToolResultStore,
    ToolResultStoreError,
)
from app.services.storage import agent_storage_key, get_storage_backend
from app.services.storage_runtime.base import StorageBackend
from app.services.workspace_collaboration import normalize_workspace_path


ReferenceExists = Callable[[str, uuid.UUID, uuid.UUID], Awaitable[bool]]
_STABLE_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_HTTP_EVIDENCE_TOOL_NAMES = frozenset(
    {"read_webpage", "upload_image", "publish_page"}
)


def _refs(metadata: object, field: str) -> tuple[str, ...] | None:
    if not isinstance(metadata, Mapping):
        return ()
    value = metadata.get(field, [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(str(item).strip() for item in value)


def _async_pending_operation(execution: AgentToolExecution) -> dict | None:
    metadata = getattr(execution, "result_metadata", None)
    if (
        execution.status != "started"
        or not isinstance(metadata, Mapping)
        or metadata.get("runtime_async_pending") is not True
    ):
        return None
    operation = metadata.get("async_operation")
    if not isinstance(operation, Mapping) or operation.get("version") != 1:
        return None
    operation_key = operation.get("operation_key")
    operation_id = operation.get("operation_id")
    state = operation.get("state")
    poll = operation.get("poll")
    if (
        not isinstance(operation_key, str)
        or not operation_key
        or not isinstance(operation_id, str)
        or not operation_id
        or not isinstance(state, str)
        or not state
        or not isinstance(poll, Mapping)
    ):
        return None
    tool = poll.get("tool")
    arguments = poll.get("arguments")
    interval_ms = poll.get("interval_ms")
    if (
        not isinstance(tool, str)
        or not tool
        or not isinstance(arguments, Mapping)
        or isinstance(interval_ms, bool)
        or not isinstance(interval_ms, int)
        or interval_ms < 0
    ):
        return None
    return {
        "operation_key": operation_key,
        "operation_id": operation_id,
        "state": state,
        "poll": {
            "tool": tool,
            "arguments": dict(arguments),
            "interval_ms": interval_ms,
        },
    }


@dataclass(frozen=True, slots=True)
class _RunReferenceScope:
    agent_id: uuid.UUID
    executions: tuple[AgentToolExecution, ...]


def _safe_agent_reference_path(raw_path: str) -> str | None:
    try:
        decoded = unquote(raw_path)
    except Exception:
        return None
    decoded = decoded.replace("\\", "/").strip().lstrip("/")
    if not decoded or any(ord(character) < 32 for character in decoded):
        return None
    if any(part == ".." for part in decoded.split("/")):
        return None
    normalized = normalize_workspace_path(decoded)
    if not normalized:
        return None
    root = normalized.split("/", 1)[0].casefold()
    if (
        root == "enterprise_info"
        or root.startswith("enterprise_info_")
        or root == "runtime"
    ):
        return None
    return normalized


def _stable_reference_id(reference: str, scheme: str) -> str | None:
    try:
        parsed = urlsplit(reference)
    except ValueError:
        return None
    if (
        parsed.scheme != scheme
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not _STABLE_REFERENCE_ID_RE.fullmatch(parsed.netloc)
    ):
        return None
    return parsed.netloc


def _public_http_reference(reference: str) -> bool:
    try:
        parsed = urlsplit(reference)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not any(ord(character) < 32 for character in reference)
    )


class RuntimeToolReferenceReader:
    """Read back the concrete reference schemes emitted by typed builtins."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        storage: StorageBackend | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage or get_storage_backend()

    async def _scope(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> _RunReferenceScope | None:
        async with self._session_factory() as db:
            run_result = await db.execute(
                select(AgentRun.agent_id)
                .join(AgentModel, AgentModel.id == AgentRun.agent_id)
                .where(
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.id == run_id,
                    AgentModel.tenant_id == tenant_id,
                )
            )
            agent_id = run_result.scalar_one_or_none()
            if not isinstance(agent_id, uuid.UUID):
                return None
            execution_result = await db.execute(
                select(AgentToolExecution).where(
                    AgentToolExecution.tenant_id == tenant_id,
                    AgentToolExecution.run_id == run_id,
                    AgentToolExecution.status == "succeeded",
                )
            )
            executions = tuple(execution_result.scalars().all())
        return _RunReferenceScope(agent_id=agent_id, executions=executions)

    @staticmethod
    def _owners(
        scope: _RunReferenceScope,
        reference: str,
        *,
        fields: tuple[str, ...],
        tool_names: frozenset[str] | None = None,
    ) -> tuple[AgentToolExecution, ...]:
        owners: list[AgentToolExecution] = []
        for execution in scope.executions:
            if execution.status != "succeeded":
                continue
            if tool_names is not None and execution.tool_name not in tool_names:
                continue
            metadata = getattr(execution, "result_metadata", None)
            for field in fields:
                values = _refs(metadata, field)
                if values is not None and reference in values:
                    owners.append(execution)
                    break
        return tuple(owners)

    async def _storage_file_readable(
        self,
        agent_id: uuid.UUID,
        path: str,
    ) -> bool:
        normalized = _safe_agent_reference_path(path)
        if normalized is None:
            return False
        key = agent_storage_key(agent_id, normalized)
        expected_prefix = f"{agent_id}/"
        if not key.startswith(expected_prefix):
            return False
        try:
            version = await self._storage.get_version(key)
            if not version.exists or version.is_dir:
                return False
            await self._storage.read_bytes(key)
        except Exception:
            return False
        return True

    async def _workspace_reference_exists(
        self,
        scope: _RunReferenceScope,
        reference: str,
    ) -> bool:
        try:
            parsed = urlsplit(reference)
        except ValueError:
            return False
        if (
            parsed.scheme != "workspace"
            or parsed.netloc != str(scope.agent_id)
            or parsed.query
            or parsed.fragment
        ):
            return False
        if not self._owners(
            scope,
            reference,
            fields=("artifact_refs", "evidence_refs"),
        ):
            return False
        return await self._storage_file_readable(scope.agent_id, parsed.path)

    async def _published_page_exists(
        self,
        scope: _RunReferenceScope,
        tenant_id: uuid.UUID,
        short_id: str,
    ) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                select(PublishedPage).where(
                    PublishedPage.short_id == short_id,
                    PublishedPage.agent_id == scope.agent_id,
                    PublishedPage.tenant_id == tenant_id,
                )
            )
            page = result.scalar_one_or_none()
        if page is None or not isinstance(page.source_path, str):
            return False
        return await self._storage_file_readable(scope.agent_id, page.source_path)

    async def _imagekit_details_match(
        self,
        *,
        agent_id: uuid.UUID,
        file_id: str,
        expected_url: str,
    ) -> bool:
        if not _public_http_reference(expected_url):
            return False
        try:
            from app.services.agent_tools import _get_tool_config

            config = await _get_tool_config(agent_id, "upload_image") or {}
            private_key = config.get("private_key")
            if not isinstance(private_key, str) or not private_key:
                return False
            endpoint = config.get("url_endpoint")
            if isinstance(endpoint, str) and endpoint.strip():
                normalized_endpoint = endpoint.strip().rstrip("/")
                if expected_url != normalized_endpoint and not expected_url.startswith(
                    normalized_endpoint + "/"
                ):
                    return False

            import httpx

            # ImageKit Get File Details API:
            # https://imagekit.io/docs/api-reference/digital-asset-management-dam/managing-assets/get-file-details
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    "https://api.imagekit.io/v1/files/"
                    f"{quote(file_id, safe='')}/details",
                    auth=(private_key, ""),
                    headers={"Accept": "application/json"},
                )
            if response.status_code != 200:
                return False
            payload = response.json()
        except Exception:
            return False
        return bool(
            isinstance(payload, Mapping)
            and payload.get("fileId") == file_id
            and payload.get("url") == expected_url
        )

    async def _imagekit_reference_exists(
        self,
        scope: _RunReferenceScope,
        reference: str,
    ) -> bool:
        file_id = _stable_reference_id(reference, "imagekit")
        if file_id is None:
            return False
        owners = self._owners(
            scope,
            reference,
            fields=("artifact_refs",),
            tool_names=frozenset({"upload_image"}),
        )
        for execution in owners:
            evidence = _refs(execution.result_metadata, "evidence_refs") or ()
            for expected_url in evidence:
                if await self._imagekit_details_match(
                    agent_id=scope.agent_id,
                    file_id=file_id,
                    expected_url=expected_url,
                ):
                    return True
        return False

    async def _published_reference_exists(
        self,
        scope: _RunReferenceScope,
        tenant_id: uuid.UUID,
        reference: str,
    ) -> bool:
        short_id = _stable_reference_id(reference, "published-page")
        if short_id is None:
            return False
        if not self._owners(
            scope,
            reference,
            fields=("artifact_refs", "evidence_refs"),
            tool_names=frozenset({"publish_page", "list_published_pages"}),
        ):
            return False
        return await self._published_page_exists(scope, tenant_id, short_id)

    async def _http_evidence_exists(
        self,
        scope: _RunReferenceScope,
        tenant_id: uuid.UUID,
        reference: str,
    ) -> bool:
        if not _public_http_reference(reference):
            return False
        owners = self._owners(
            scope,
            reference,
            fields=("evidence_refs",),
            tool_names=_HTTP_EVIDENCE_TOOL_NAMES,
        )
        for execution in owners:
            if execution.tool_name == "read_webpage":
                # read_webpage validates every emitted final URL as public at
                # execution time. Verification consumes its ledger-bound
                # snapshot and deliberately performs no network request.
                summary = getattr(execution, "result_summary", None)
                result_ref = getattr(execution, "result_ref", None)
                if (
                    isinstance(summary, str) and summary.strip()
                ) or (
                    isinstance(result_ref, str)
                    and result_ref.startswith("tool-result://")
                ):
                    return True
                continue
            artifacts = _refs(execution.result_metadata, "artifact_refs") or ()
            if execution.tool_name == "upload_image":
                for artifact in artifacts:
                    file_id = _stable_reference_id(artifact, "imagekit")
                    if file_id is not None and await self._imagekit_details_match(
                        agent_id=scope.agent_id,
                        file_id=file_id,
                        expected_url=reference,
                    ):
                        return True
            if execution.tool_name == "publish_page":
                for artifact in artifacts:
                    short_id = _stable_reference_id(artifact, "published-page")
                    if short_id is None:
                        continue
                    parsed = urlsplit(reference)
                    if parsed.path != f"/p/{short_id}":
                        continue
                    if await self._published_page_exists(
                        scope,
                        tenant_id,
                        short_id,
                    ):
                        return True
        return False

    async def reference_exists(
        self,
        reference: str,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> bool:
        """Return true only for a current-run reference with a trusted reader."""
        if not isinstance(reference, str) or not reference.strip():
            return False
        try:
            scope = await self._scope(tenant_id, run_id)
            if scope is None:
                return False
            normalized_reference = reference.strip()
            scheme = urlsplit(normalized_reference).scheme
            if scheme == "workspace":
                return await self._workspace_reference_exists(
                    scope,
                    normalized_reference,
                )
            if scheme == "published-page":
                return await self._published_reference_exists(
                    scope,
                    tenant_id,
                    normalized_reference,
                )
            if scheme == "imagekit":
                return await self._imagekit_reference_exists(
                    scope,
                    normalized_reference,
                )
            if scheme in {"http", "https"}:
                return await self._http_evidence_exists(
                    scope,
                    tenant_id,
                    normalized_reference,
                )
            # tool-result:// is resolved by ToolResultStore in the verifier.
            return False
        except Exception:
            return False


class ToolLedgerRuntimeVerifier:
    """Verify only deterministic protocol, ledger, and reference facts."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        result_store: ToolResultStore | None = None,
        reference_exists: ReferenceExists | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._result_store = result_store
        self._reference_exists = reference_exists

    async def verify(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        candidate: str,
    ) -> VerificationResult:
        if not candidate.strip():
            return VerificationResult(
                outcome="repair",
                reason="finish content is empty",
                details={"code": "empty_finish"},
            )
        if state["lifecycle"].get("pending_tool_calls"):
            return VerificationResult(
                outcome="repair",
                reason="pending tool calls remain",
                details={"code": "pending_tools"},
            )
        try:
            tenant_id = uuid.UUID(context.tenant_id)
            run_id = uuid.UUID(context.run_id)
        except (TypeError, ValueError) as exc:
            return VerificationResult(
                outcome="fail",
                reason="Runtime verification identity is invalid",
                details={"code": "invalid_runtime_identity", "error_class": type(exc).__name__},
            )

        async with self._session_factory() as db:
            result = await db.execute(
                select(AgentToolExecution).where(
                    AgentToolExecution.tenant_id == tenant_id,
                    AgentToolExecution.run_id == run_id,
                )
            )
            executions = list(result.scalars().all())

        async_pending_by_key: dict[str, dict] = {}
        for execution in executions:
            operation = _async_pending_operation(execution)
            if operation is not None:
                async_pending_by_key[operation["operation_key"]] = operation
        unsettled = sorted(
            execution.tool_call_id
            for execution in executions
            if execution.status in {"started", "unknown"}
            and _async_pending_operation(execution) is None
        )
        if unsettled:
            return VerificationResult(
                outcome="fail",
                reason="unsettled tool executions require reconciliation",
                details={
                    "code": "unsettled_tool_execution",
                    "tool_call_ids": unsettled,
                },
            )
        if async_pending_by_key:
            operations = list(async_pending_by_key.values())
            actions = "; ".join(
                f"call {operation['poll']['tool']} with arguments "
                f"{json.dumps(operation['poll']['arguments'], ensure_ascii=False, sort_keys=True)}"
                for operation in operations
            )
            return VerificationResult(
                outcome="repair",
                reason=(
                    "Async tool operations are still pending. Do not finish yet; "
                    f"poll them to a declared terminal state: {actions}."
                ),
                details={
                    "code": "async_tool_pending",
                    "operations": operations,
                },
            )
        invalid_statuses = sorted(
            execution.tool_call_id
            for execution in executions
            if execution.status not in {"succeeded", "failed"}
        )
        if invalid_statuses:
            return VerificationResult(
                outcome="fail",
                reason="tool ledger contains an invalid status",
                details={
                    "code": "invalid_tool_execution_status",
                    "tool_call_ids": invalid_statuses,
                },
            )

        artifact_refs: list[str] = []
        evidence_refs: list[str] = []
        for execution in executions:
            if execution.status != "succeeded":
                continue
            metadata = getattr(execution, "result_metadata", None)
            execution_artifacts = _refs(metadata, "artifact_refs")
            execution_evidence = _refs(metadata, "evidence_refs")
            if execution_artifacts is None or execution_evidence is None:
                return VerificationResult(
                    outcome="repair",
                    reason="a succeeded tool has malformed artifact/evidence refs",
                    details={
                        "code": "malformed_tool_references",
                        "tool_call_id": execution.tool_call_id,
                    },
                )
            artifact_refs.extend(execution_artifacts)
            evidence_refs.extend(execution_evidence)
            if execution.result_ref and execution.result_ref.startswith("tool-result://"):
                if self._result_store is None:
                    return VerificationResult(
                        outcome="fail",
                        reason="private tool result cannot be verified",
                        details={
                            "code": "tool_result_store_unavailable",
                            "tool_call_id": execution.tool_call_id,
                        },
                    )
                try:
                    await self._result_store.resolve(
                        execution.result_ref,
                        tenant_id=tenant_id,
                        run_id=run_id,
                    )
                except ToolResultStoreError as exc:
                    return VerificationResult(
                        outcome="repair",
                        reason="a referenced private tool result is unreadable",
                        details={
                            "code": exc.code,
                            "tool_call_id": execution.tool_call_id,
                        },
                    )

        artifact_refs = list(dict.fromkeys(artifact_refs))
        evidence_refs = list(dict.fromkeys(evidence_refs))
        for reference in (*artifact_refs, *evidence_refs):
            if reference.startswith("tool-result://"):
                if self._result_store is None:
                    return VerificationResult(
                        outcome="fail",
                        reason="private tool reference cannot be verified",
                        details={"code": "tool_result_store_unavailable"},
                    )
                try:
                    await self._result_store.resolve(
                        reference,
                        tenant_id=tenant_id,
                        run_id=run_id,
                    )
                except ToolResultStoreError as exc:
                    return VerificationResult(
                        outcome="repair",
                        reason="an artifact/evidence reference is unreadable",
                        details={"code": exc.code, "reference": reference},
                    )
            elif self._reference_exists is None:
                return VerificationResult(
                    outcome="repair",
                    reason="an artifact/evidence reference has no trusted reader",
                    details={
                        "code": "unverifiable_tool_reference",
                        "reference": reference,
                    },
                )
            else:
                try:
                    readable = await self._reference_exists(
                        reference,
                        tenant_id,
                        run_id,
                    )
                except Exception as exc:
                    return VerificationResult(
                        outcome="repair",
                        reason="an artifact/evidence reference could not be read",
                        details={
                            "code": "tool_reference_read_failed",
                            "error_class": type(exc).__name__,
                            "reference": reference,
                        },
                    )
                if not readable:
                    return VerificationResult(
                        outcome="repair",
                        reason="an artifact/evidence reference is not readable",
                        details={
                            "code": "tool_reference_unreadable",
                            "reference": reference,
                        },
                    )

        return VerificationResult(
            outcome="pass",
            details={
                "code": "deterministic_checks_passed",
                "artifact_refs": artifact_refs,
                "evidence_refs": evidence_refs,
            },
        )


__all__ = ["RuntimeToolReferenceReader", "ToolLedgerRuntimeVerifier"]
