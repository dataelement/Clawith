"""Durable idempotency decisions for Runtime tool executions.

The ledger is deliberately narrower than a trace system.  It answers one
question before a tool node performs work: may this exact model tool call be
executed, or must the Runtime reuse/reconcile an earlier outcome?
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
import unicodedata
from typing import Any, Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.agent_tool_execution import AgentToolExecution


ToolExecutionStatus = Literal[
    "not_started",
    "started",
    "succeeded",
    "failed",
    "unknown",
]
SideEffectClassification = Literal["read", "write", "external_write"]
RetryPolicy = Literal["safe", "conditional", "never"]
SAFE_READ_MAX_ATTEMPTS = 3

_PERSISTED_STATUSES = frozenset({"started", "succeeded", "failed", "unknown"})
_SIDE_EFFECT_CLASSIFICATIONS = frozenset({"read", "write", "external_write"})
_RETRY_POLICIES = frozenset({"safe", "conditional", "never"})
_METADATA_KEY = "__clawith_tool_execution__"
_METADATA_VERSION = 1
_RESULT_METADATA_MAX_BYTES = 16 * 1024
_RESULT_METADATA_KEYS = frozenset(
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
        "archive_status",
        "archive_error_code",
        "message_id",
        "accepted_recipients",
        "refused_recipients",
        "tenant_id",
        "period_start",
        "period_end",
        "objective_count",
        "kr_count",
        "objective_id",
        "kr_id",
        "report_id",
        "progress_log_id",
        "owner_type",
        "owner_id",
        "member_type",
        "member_id",
        "report_date",
        "previous_value",
        "current_value",
        "target_value",
        "status",
        "changed_fields",
        "content_truncated",
        "okr_content_hash",
        "stored_character_count",
        "source",
        "operation_id",
        "updated_count",
        "skipped_count",
        "error_count",
        "updated_refs",
        "report_type",
        "workspace_path",
        "db_status",
        "projection_status",
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
        "runtime_attempt_count",
        "runtime_retry_pending",
        "runtime_retry_exhausted",
        "last_error_code",
        "runtime_async_pending",
        "async_operation",
        "async_poll_due_at",
        "async_poll_correlation_id",
        "async_poll_call_id",
        "async_poll_scheduled",
        "external_reconciliation",
        "reconciled_by_user_id",
        "reconciled_at",
        "reconciliation_note",
        "original_status",
        "original_completed_at",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "password",
        "passwd",
        "authorization",
        "cookie",
        "setcookie",
        "dsn",
        "secret",
        "clientsecret",
        "privatekey",
        "signedurl",
        "signature",
        "sig",
        "xamzsignature",
        "xamzcredential",
        "xamzsecuritytoken",
        "xgoogsignature",
        "xgoogcredential",
        "xgoogsecuritytoken",
    }
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"authorization|cookie|dsn|client[_-]?secret)\b(\s*[:=]\s*)"
    r"(?:bearer\s+)?([^\s,;]+)"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_DSN_RE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s<>\"']+"
)


class ToolExecutionError(RuntimeError):
    """A stable tool-ledger contract was rejected without executing the tool."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RetryableToolNodeError(RuntimeError):
    """Ask LangGraph to retry one safe-read Tool node attempt."""

    def __init__(self, *, tool_call_id: str, error_code: str | None) -> None:
        super().__init__("safe read tool attempt is eligible for Runtime retry")
        self.tool_call_id = tool_call_id
        self.error_code = error_code


class ToolExecutionReconciliationPending(RuntimeError):
    """Recovery must retry without pretending that a tool outcome is known."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        defer_without_attempt: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.defer_without_attempt = defer_without_attempt


def _sensitive_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalized in _SENSITIVE_KEYS


def _sanitize_url(value: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value, 0
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.query:
        return value, 0
    redactions = 0
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _sensitive_key(key):
            query.append((key, "[REDACTED]"))
            redactions += 1
        else:
            query.append((key, item))
    if not redactions:
        return value, 0
    return (
        urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        ),
        redactions,
    )


def _redact_text(value: str) -> tuple[str, int]:
    count = 0

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}[REDACTED]"

    redacted = _SECRET_ASSIGNMENT_RE.sub(assignment, value)

    def dsn(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        scheme = match.group(0).split(":", 1)[0]
        return f"{scheme}://[REDACTED]"

    redacted = _DSN_RE.sub(dsn, redacted)

    def url(match: re.Match[str]) -> str:
        nonlocal count
        normalized, replacements = _sanitize_url(match.group(0))
        count += replacements
        return normalized

    return _URL_RE.sub(url, redacted), count


def _normalize_text(value: str, *, redact: bool) -> tuple[str, int, int, int]:
    nul_replacements = 0
    control_replacements = 0
    output: list[str] = []
    for character in value:
        if character == "\x00":
            output.append("\ufffd")
            nul_replacements += 1
        elif character in {"\t", "\n", "\r"}:
            output.append(character)
        elif unicodedata.category(character) == "Cc":
            output.append("\ufffd")
            control_replacements += 1
        else:
            output.append(character)
    normalized = "".join(output)
    if not redact:
        return normalized, nul_replacements, control_replacements, 0
    redacted, redaction_count = _redact_text(normalized)
    return redacted, nul_replacements, control_replacements, redaction_count


def _sanitize_json(value: Any, *, sensitive: bool = False) -> Any:
    if sensitive:
        return "[REDACTED]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key, _, _, _ = _normalize_text(str(key), redact=False)
            if normalized_key in sanitized:
                raise ToolExecutionError(
                    "invalid_tool_execution_input",
                    "JSON keys collide after control-character normalization",
                )
            sanitized[normalized_key] = _sanitize_json(
                item,
                sensitive=_sensitive_key(normalized_key),
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        normalized, _, _, _ = _normalize_text(value, redact=True)
        return normalized
    return value


def sanitize_tool_arguments(
    arguments: dict[str, Any],
    *,
    sensitive_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a JSON-safe recursive secret-redacted ledger copy."""
    copied = _json_copy(arguments, field="arguments")
    sanitized = _sanitize_json(copied)
    if not isinstance(sanitized, dict):  # pragma: no cover - guarded by _json_copy
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "arguments must normalize to a JSON object",
        )
    for dotted_path in sensitive_paths:
        parts = [part for part in dotted_path.split(".") if part]
        if not parts:
            continue
        current: Any = sanitized
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if isinstance(current, dict) and parts[-1] in current:
            current[parts[-1]] = "[REDACTED]"
    return _json_copy(sanitized, field="sanitized_arguments")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = "\n...[tool result archived]...\n"
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return encoded[:max_bytes].decode("utf-8", errors="ignore")
    remaining = max_bytes - len(marker_bytes)
    head_size = (remaining * 3) // 5
    tail_size = remaining - head_size
    head = encoded[:head_size].decode("utf-8", errors="ignore")
    tail = encoded[-tail_size:].decode("utf-8", errors="ignore")
    while len((head + marker + tail).encode("utf-8")) > max_bytes and tail:
        tail = tail[:-1]
    return head + marker + tail


def _bounded_result_metadata(value: dict[str, Any]) -> dict[str, Any]:
    filtered = _sanitize_json(
        {
            key: deepcopy(item)
            for key, item in value.items()
            if key in _RESULT_METADATA_KEYS
        }
    )
    if not isinstance(filtered, dict):  # pragma: no cover - constructed as dict
        raise ToolExecutionError(
            "invalid_tool_outcome_metadata",
            "tool outcome metadata must be an object",
        )
    try:
        encoded = json.dumps(
            filtered,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(
            "invalid_tool_outcome_metadata",
            "tool outcome metadata must contain finite JSON values",
        ) from exc
    if len(encoded) > _RESULT_METADATA_MAX_BYTES:
        raise ToolExecutionError(
            "invalid_tool_outcome_metadata",
            "tool outcome metadata exceeds its storage limit",
        )
    copied = json.loads(encoded)
    if not isinstance(copied, dict):  # pragma: no cover - constructed as dict
        raise ToolExecutionError(
            "invalid_tool_outcome_metadata",
            "tool outcome metadata must be an object",
        )
    return copied


def normalize_tool_outcome(
    outcome: ToolExecutionOutcome,
    *,
    effect: SideEffectClassification,
    retry_policy: RetryPolicy,
    inline_max_bytes: int,
) -> tuple[ToolExecutionOutcome, str | None]:
    """Normalize one typed result and return any body requiring private archive."""
    if inline_max_bytes <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "inline_max_bytes must be positive",
        )
    if outcome.status not in {"succeeded", "failed", "pending", "unknown"}:
        raise ToolExecutionError(
            "invalid_tool_outcome",
            f"unsupported tool outcome status: {outcome.status}",
        )
    if outcome.result_summary is not None and not isinstance(
        outcome.result_summary, str
    ):
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "tool outcome summary must be a string or null",
        )
    if outcome.result_ref is not None and not isinstance(outcome.result_ref, str):
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "tool outcome result_ref must be a string or null",
        )
    if outcome.error_code is not None and not isinstance(outcome.error_code, str):
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "tool outcome error_code must be a string or null",
        )
    if not isinstance(outcome.retryable, bool) or not isinstance(
        outcome.metadata, dict
    ):
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "tool outcome retryable/metadata types are invalid",
        )
    if outcome.private_binary is not None and not isinstance(
        outcome.private_binary,
        bytes,
    ):
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "private binary tool outcome content must be bytes or null",
        )
    if outcome.private_binary is not None and outcome.status != "succeeded":
        raise ToolExecutionError(
            "invalid_tool_outcome",
            "private binary tool outcome content requires succeeded status",
        )
    summary = outcome.result_summary
    nul_replacements = control_replacements = redaction_count = 0
    if summary is not None:
        summary, nul_replacements, control_replacements, redaction_count = (
            _normalize_text(summary, redact=True)
        )
    refs: list[tuple[str, ...]] = []
    for raw_refs in (outcome.artifact_refs, outcome.evidence_refs):
        if not isinstance(raw_refs, (tuple, list)):
            raise ToolExecutionError(
                "invalid_tool_outcome",
                "artifact and evidence refs must be arrays",
            )
        normalized_refs: list[str] = []
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, str) or not raw_ref.strip():
                raise ToolExecutionError(
                    "invalid_tool_outcome",
                    "artifact and evidence refs must be non-empty strings",
                )
            ref, nul_count, control_count, ref_redactions = _normalize_text(
                raw_ref.strip(),
                redact=True,
            )
            nul_replacements += nul_count
            control_replacements += control_count
            redaction_count += ref_redactions
            normalized_refs.append(ref)
        refs.append(tuple(normalized_refs))
    result_ref = outcome.result_ref
    if result_ref is not None:
        result_ref, nul_count, control_count, ref_redactions = _normalize_text(
            result_ref.strip(),
            redact=True,
        )
        nul_replacements += nul_count
        control_replacements += control_count
        redaction_count += ref_redactions
        if not result_ref:
            result_ref = None
    error_code = outcome.error_code
    if error_code is not None:
        error_code, nul_count, control_count, _ = _normalize_text(
            error_code.strip(),
            redact=False,
        )
        nul_replacements += nul_count
        control_replacements += control_count
        error_code = error_code[:200] or None

    archived_body: str | None = None
    summary_truncated = False
    content_hash: str | None = None
    if summary is not None:
        content_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        if len(summary.encode("utf-8")) > inline_max_bytes:
            summary_truncated = True
            if result_ref is None:
                archived_body = summary
            summary = _truncate_utf8(summary, inline_max_bytes)

    retryable = (
        outcome.retryable
        and outcome.status == "failed"
        and effect == "read"
        and retry_policy == "safe"
    )
    metadata = _bounded_result_metadata(
        {
            **outcome.metadata,
            "error_code": error_code,
            "retryable": retryable,
            "artifact_refs": list(refs[0]),
            "evidence_refs": list(refs[1]),
            "nul_replacements": nul_replacements,
            "control_replacements": control_replacements,
            "redaction_count": redaction_count,
            "summary_truncated": summary_truncated,
            "content_hash": content_hash,
            "archive_status": (
                "pending"
                if archived_body is not None
                else "external_ref"
                if result_ref is not None and summary_truncated
                else "inline"
            ),
        }
    )
    return (
        ToolExecutionOutcome(
            status=outcome.status,
            result_summary=summary,
            result_ref=result_ref,
            error_code=error_code,
            retryable=retryable,
            artifact_refs=refs[0],
            evidence_refs=refs[1],
            metadata=metadata,
            private_binary=outcome.private_binary,
        ),
        archived_body,
    )


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """The durable, safe-to-reuse portion of a typed tool outcome."""

    status: Literal["succeeded", "failed", "pending", "unknown"]
    result_summary: str | None
    result_ref: str | None
    error_code: str | None = None
    retryable: bool = False
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    # Ephemeral handoff to ToolResultStore. It is archived before ledger
    # settlement and never serialized into messages or result metadata.
    private_binary: bytes | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def summary(self) -> str | None:
        """Canonical public name while old callers migrate from result_summary."""
        return self.result_summary


@dataclass(frozen=True, slots=True)
class ToolExecutionInspection:
    """Current ledger state; ``not_started`` is represented by no table row."""

    status: ToolExecutionStatus
    execution: AgentToolExecution | None


@dataclass(frozen=True, slots=True)
class ToolExecutionReservation:
    """Deterministic decision returned before a caller executes a tool."""

    execution: AgentToolExecution
    created: bool
    retrying: bool
    reusable_result: ToolExecutionOutcome | None
    prior_failure: ToolExecutionOutcome | None
    blocked: bool
    reconciliation_required: bool
    requires_confirmation: bool
    error_code: str | None

    @property
    def status(self) -> str:
        return self.execution.status

    @property
    def can_execute(self) -> bool:
        """True only for a newly persisted reservation or an explicit safe retry."""
        return not self.blocked and self.reusable_result is None


@dataclass(frozen=True, slots=True)
class ToolExecutionTakeover:
    """Atomic recovery-fence decision for an existing ledger position."""

    execution: AgentToolExecution
    acquired: bool
    active: bool
    terminal_outcome: ToolExecutionOutcome | None


def _require_text(value: str, *, field: str, max_length: int) -> None:
    if not value or not value.strip():
        raise ToolExecutionError("invalid_tool_execution_input", f"{field} must not be blank")
    if len(value) > max_length:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"{field} exceeds its {max_length}-character storage limit",
        )


def _require_optional_text(value: str | None, *, field: str, max_length: int) -> None:
    if value is not None:
        _require_text(value, field=field, max_length=max_length)


def _metadata_count(metadata: dict[str, Any] | None, field_name: str) -> int:
    value = (metadata or {}).get(field_name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _attempt_count(execution: AgentToolExecution) -> int:
    value = getattr(execution, "attempt_count", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _json_copy(value: dict[str, Any], *, field: str) -> dict[str, Any]:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"{field} must be a JSON object with finite values",
        ) from exc
    if not isinstance(copied, dict):
        raise ToolExecutionError("invalid_tool_execution_input", f"{field} must be a JSON object")
    return copied


def fingerprint_arguments(arguments: dict[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint without persisting raw arguments."""
    canonical = json.dumps(
        _json_copy(arguments, field="arguments"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _stored_arguments(
    sanitized_arguments: dict[str, Any] | None,
    *,
    side_effect_classification: str,
    retry_policy: str,
) -> dict[str, Any]:
    del side_effect_classification, retry_policy
    return (
        _json_copy(sanitized_arguments, field="sanitized_arguments")
        if sanitized_arguments is not None
        else {}
    )


def _execution_metadata(execution: AgentToolExecution) -> tuple[str, str]:
    effect = getattr(execution, "effect", None)
    retry_policy = getattr(execution, "retry_policy", None)
    if effect in _SIDE_EFFECT_CLASSIFICATIONS and retry_policy in _RETRY_POLICIES:
        return str(effect), str(retry_policy)
    stored = execution.sanitized_arguments
    metadata = stored.get(_METADATA_KEY) if isinstance(stored, dict) else None
    if not isinstance(metadata, dict) or metadata.get("version") != _METADATA_VERSION:
        # Old or malformed rows are treated as external writes.  This is the
        # conservative boundary for reconciliation and never enables a retry.
        return "external_write", "never"
    effect = metadata.get("side_effect_classification")
    retry_policy = metadata.get("retry_policy")
    if effect not in _SIDE_EFFECT_CLASSIFICATIONS or retry_policy not in _RETRY_POLICIES:
        return "external_write", "never"
    return str(effect), str(retry_policy)


def execution_policy(execution: AgentToolExecution) -> tuple[str, str]:
    """Read explicit policy columns with conservative legacy fallback."""
    return _execution_metadata(execution)


def _execution_arguments(execution: AgentToolExecution) -> dict[str, Any]:
    stored = execution.sanitized_arguments
    if not isinstance(stored, dict):
        return {}
    metadata = stored.get(_METADATA_KEY)
    if isinstance(metadata, dict) and "arguments" in stored:
        legacy = stored.get("arguments")
        return legacy if isinstance(legacy, dict) else {}
    return stored


def _validate_request(
    *,
    tool_call_id: str,
    tool_name: str,
    assistant_message_id: str,
    side_effect_classification: str,
    retry_policy: str,
    request_ref: str | None,
    lease_owner: str,
    lease_ttl_seconds: int,
) -> None:
    _require_text(tool_call_id, field="tool_call_id", max_length=255)
    _require_text(tool_name, field="tool_name", max_length=200)
    _require_text(assistant_message_id, field="assistant_message_id", max_length=255)
    _require_text(lease_owner, field="lease_owner", max_length=128)
    _require_optional_text(request_ref, field="request_ref", max_length=500)
    if side_effect_classification not in _SIDE_EFFECT_CLASSIFICATIONS:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"unsupported side_effect_classification: {side_effect_classification}",
        )
    if retry_policy not in _RETRY_POLICIES:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            f"unsupported retry_policy: {retry_policy}",
        )
    if lease_ttl_seconds <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "lease_ttl_seconds must be positive",
        )


async def _require_run(db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    result = await db.execute(select(AgentRun.id).where(AgentRun.tenant_id == tenant_id, AgentRun.id == run_id))
    if result.scalar_one_or_none() is None:
        raise ToolExecutionError(
            "run_not_found",
            f"run {run_id} does not exist in tenant {tenant_id}",
        )


def _execution_statement(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    lock: bool,
):
    statement = select(AgentToolExecution).where(
        AgentToolExecution.tenant_id == tenant_id,
        AgentToolExecution.run_id == run_id,
        AgentToolExecution.tool_call_id == tool_call_id,
    )
    return statement.with_for_update() if lock else statement


async def _find_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    lock: bool,
) -> AgentToolExecution | None:
    result = await db.execute(
        _execution_statement(
            tenant_id=tenant_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            lock=lock,
        )
    )
    return result.scalar_one_or_none()


async def inspect_tool_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
) -> ToolExecutionInspection:
    """Inspect one tenant/run/call ledger position without claiming execution."""
    _require_text(tool_call_id, field="tool_call_id", max_length=255)
    await _require_run(db, tenant_id=tenant_id, run_id=run_id)
    execution = await _find_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        lock=False,
    )
    if execution is None:
        return ToolExecutionInspection(status="not_started", execution=None)
    if execution.status not in _PERSISTED_STATUSES:
        raise ToolExecutionError(
            "invalid_tool_execution_state",
            f"tool execution {execution.id} has unsupported status {execution.status}",
        )
    return ToolExecutionInspection(status=execution.status, execution=execution)  # type: ignore[arg-type]


def _require_exact_request(
    existing: AgentToolExecution,
    *,
    tool_name: str,
    assistant_message_id: str,
    arguments_hash: str,
    stored_arguments: dict[str, Any],
    request_ref: str | None,
    side_effect_classification: str,
    retry_policy: str,
) -> None:
    expected = {
        "tool_name": tool_name,
        "assistant_message_id": assistant_message_id,
        "arguments_hash": arguments_hash,
        "request_ref": request_ref,
    }
    mismatched = [field for field, value in expected.items() if getattr(existing, field) != value]
    if _execution_arguments(existing) != stored_arguments:
        mismatched.append("sanitized_arguments")
    if _execution_metadata(existing) != (side_effect_classification, retry_policy):
        mismatched.extend(("effect", "retry_policy"))
    if mismatched:
        raise ToolExecutionError(
            "tool_call_idempotency_mismatch",
            "tool_call_id already exists with different immutable inputs: " + ", ".join(sorted(mismatched)),
        )


def _outcome(execution: AgentToolExecution) -> ToolExecutionOutcome:
    metadata = getattr(execution, "result_metadata", None)
    metadata = _bounded_result_metadata(metadata if isinstance(metadata, dict) else {})
    artifact_refs = metadata.get("artifact_refs", [])
    evidence_refs = metadata.get("evidence_refs", [])
    status = (
        "pending"
        if execution.status == "started"
        and metadata.get("runtime_async_pending") is True
        else execution.status
    )
    return ToolExecutionOutcome(
        status=status,  # type: ignore[arg-type]
        result_summary=execution.result_summary,
        result_ref=execution.result_ref,
        error_code=(
            str(metadata["error_code"])
            if isinstance(metadata.get("error_code"), str)
            else None
        ),
        retryable=metadata.get("retryable") is True,
        artifact_refs=tuple(
            str(value) for value in artifact_refs if isinstance(value, str)
        ) if isinstance(artifact_refs, list) else (),
        evidence_refs=tuple(
            str(value) for value in evidence_refs if isinstance(value, str)
        ) if isinstance(evidence_refs, list) else (),
        metadata=metadata,
    )


def execution_outcome(execution: AgentToolExecution) -> ToolExecutionOutcome:
    """Rehydrate the shared typed outcome from one terminal ledger fact."""
    return _outcome(execution)


def _decision_for_existing(
    execution: AgentToolExecution,
    *,
    resume_safe_read: bool,
    lease_owner: str,
    lease_expires_at: datetime,
    now: datetime,
) -> ToolExecutionReservation:
    effect, retry_policy = _execution_metadata(execution)
    if execution.status == "succeeded":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=_outcome(execution),
            prior_failure=None,
            blocked=False,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code=None,
        )
    if execution.status == "started":
        metadata = (
            execution.result_metadata
            if isinstance(execution.result_metadata, dict)
            else {}
        )
        retry_pending = metadata.get("runtime_retry_pending") is True
        if metadata.get("runtime_async_pending") is True:
            return ToolExecutionReservation(
                execution=execution,
                created=False,
                retrying=False,
                reusable_result=_outcome(execution),
                prior_failure=None,
                blocked=False,
                reconciliation_required=False,
                requires_confirmation=False,
                error_code=None,
            )
        lease_expired = (
            execution.lease_expires_at is None
            or execution.lease_expires_at <= now
        )
        attempt_count = _attempt_count(execution)
        if (
            resume_safe_read
            and effect == "read"
            and retry_policy == "safe"
            and attempt_count < SAFE_READ_MAX_ATTEMPTS
            and retry_pending
            and lease_expired
        ):
            prior_failure = ToolExecutionOutcome(
                status="failed",
                result_summary=(
                    execution.result_summary
                    or "The previous safe read attempt did not settle."
                ),
                result_ref=None,
                error_code=(
                    str(metadata["error_code"])
                    if isinstance(metadata.get("error_code"), str)
                    else "safe_read_attempt_interrupted"
                ),
                retryable=True,
                metadata=_bounded_result_metadata(metadata),
            )
            execution.attempt_count = attempt_count + 1
            execution.result_summary = None
            execution.result_ref = None
            execution.result_metadata = {}
            execution.lease_owner = lease_owner
            execution.lease_expires_at = lease_expires_at
            execution.started_at = now
            execution.completed_at = None
            return ToolExecutionReservation(
                execution=execution,
                created=False,
                retrying=True,
                reusable_result=None,
                prior_failure=prior_failure,
                blocked=False,
                reconciliation_required=False,
                requires_confirmation=False,
                error_code=None,
            )
        if (
            resume_safe_read
            and effect == "read"
            and retry_policy == "safe"
            and attempt_count >= SAFE_READ_MAX_ATTEMPTS
            and retry_pending
            and lease_expired
        ):
            last_error_code = (
                str(metadata["error_code"])
                if isinstance(metadata.get("error_code"), str)
                else "safe_read_attempt_interrupted"
            )
            execution.status = "failed"
            execution.result_summary = (
                "The final safe read attempt did not settle before its lease "
                f"expired. Runtime automatic retries were exhausted after "
                f"{attempt_count} attempts. Do not repeat the identical tool "
                "call unchanged."
            )
            execution.result_ref = None
            execution.result_metadata = _bounded_result_metadata(
                {
                    "error_code": "tool_retry_exhausted",
                    "retryable": False,
                    "runtime_attempt_count": attempt_count,
                    "runtime_retry_exhausted": True,
                    "runtime_retry_pending": False,
                    "last_error_code": last_error_code,
                }
            )
            execution.lease_owner = None
            execution.lease_expires_at = None
            execution.completed_at = now
            return ToolExecutionReservation(
                execution=execution,
                created=False,
                retrying=False,
                reusable_result=None,
                prior_failure=_outcome(execution),
                blocked=True,
                reconciliation_required=False,
                requires_confirmation=False,
                error_code="tool_execution_failed",
            )
        if (
            resume_safe_read
            and effect == "read"
            and retry_policy == "safe"
            and lease_expired
            and not retry_pending
        ):
            # A private result envelope may already prove success even though
            # ledger settlement crashed. The caller must probe reconciliation
            # before this receipt can be closed or exposed to the model.
            return ToolExecutionReservation(
                execution=execution,
                created=False,
                retrying=False,
                reusable_result=None,
                prior_failure=None,
                blocked=True,
                reconciliation_required=True,
                requires_confirmation=False,
                error_code="safe_read_result_reconciliation_required",
            )
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=True,
            reconciliation_required=True,
            requires_confirmation=False,
            error_code="tool_execution_started",
        )
    if execution.status == "unknown":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=True,
            reconciliation_required=True,
            requires_confirmation=effect != "read",
            error_code="tool_outcome_unknown",
        )
    if execution.status == "failed":
        return ToolExecutionReservation(
            execution=execution,
            created=False,
            retrying=False,
            reusable_result=None,
            prior_failure=_outcome(execution),
            blocked=True,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code="tool_execution_failed",
        )
    raise ToolExecutionError(
        "invalid_tool_execution_state",
        f"tool execution {execution.id} has unsupported status {execution.status}",
    )


async def reserve_tool_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    assistant_message_id: str,
    arguments: dict[str, Any],
    sanitized_arguments: dict[str, Any] | None,
    request_ref: str | None,
    side_effect_classification: SideEffectClassification,
    retry_policy: RetryPolicy,
    lease_owner: str,
    lease_ttl_seconds: int,
    resume_safe_read: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ToolExecutionReservation:
    """Atomically reserve an exact tool call without committing the caller transaction.

    A returned reservation permits execution only when ``can_execute`` is true.
    Only a durable retry-pending or expired ``read + safe`` receipt may claim a
    bounded next attempt. Writes, unknown outcomes, and terminal failures are
    never reopened.
    """
    _validate_request(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        assistant_message_id=assistant_message_id,
        side_effect_classification=side_effect_classification,
        retry_policy=retry_policy,
        request_ref=request_ref,
        lease_owner=lease_owner,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    arguments_hash = fingerprint_arguments(arguments)
    stored_arguments = _stored_arguments(
        sanitized_arguments,
        side_effect_classification=side_effect_classification,
        retry_policy=retry_policy,
    )
    now = (clock or (lambda: datetime.now(UTC)))()
    lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)

    await _require_run(db, tenant_id=tenant_id, run_id=run_id)
    existing = await _find_execution(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        lock=True,
    )
    if existing is not None:
        _require_exact_request(
            existing,
            tool_name=tool_name,
            assistant_message_id=assistant_message_id,
            arguments_hash=arguments_hash,
            stored_arguments=stored_arguments,
            request_ref=request_ref,
            side_effect_classification=side_effect_classification,
            retry_policy=retry_policy,
        )
        prior_status = existing.status
        decision = _decision_for_existing(
            existing,
            resume_safe_read=resume_safe_read,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=now,
        )
        if decision.retrying or existing.status != prior_status:
            await db.flush()
        return decision

    execution = AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        assistant_message_id=assistant_message_id,
        arguments_hash=arguments_hash,
        sanitized_arguments=deepcopy(stored_arguments),
        request_ref=request_ref,
        effect=side_effect_classification,
        retry_policy=retry_policy,
        attempt_count=1,
        result_metadata={},
        status="started",
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        started_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(execution)
            await db.flush()
        return ToolExecutionReservation(
            execution=execution,
            created=True,
            retrying=False,
            reusable_result=None,
            prior_failure=None,
            blocked=False,
            reconciliation_required=False,
            requires_confirmation=False,
            error_code=None,
        )
    except IntegrityError:
        concurrent = await _find_execution(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            lock=True,
        )
        if concurrent is None:
            raise
        _require_exact_request(
            concurrent,
            tool_name=tool_name,
            assistant_message_id=assistant_message_id,
            arguments_hash=arguments_hash,
            stored_arguments=stored_arguments,
            request_ref=request_ref,
            side_effect_classification=side_effect_classification,
            retry_policy=retry_policy,
        )
        # A concurrent winner has already crossed into started.  Even when its
        # lease later expires, the losing worker may not execute the call.
        return _decision_for_existing(
            concurrent,
            resume_safe_read=False,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=now,
        )


async def _get_locked_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
) -> AgentToolExecution:
    result = await db.execute(
        select(AgentToolExecution)
        .where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.id == execution_id,
        )
        .with_for_update()
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise ToolExecutionError(
            "tool_execution_not_found",
            f"tool execution {execution_id} does not exist in tenant {tenant_id}",
        )
    return execution


def _require_lease_owner(execution: AgentToolExecution, lease_owner: str) -> None:
    _require_text(lease_owner, field="lease_owner", max_length=128)
    if execution.status != "started" or execution.lease_owner != lease_owner:
        raise ToolExecutionError(
            "tool_execution_lease_lost",
            "tool execution is not currently started by this worker",
        )


async def renew_tool_execution_lease(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    lease_ttl_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Renew the current owner's reservation without enabling another executor."""
    if lease_ttl_seconds <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "lease_ttl_seconds must be positive",
        )
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    _require_lease_owner(execution, lease_owner)
    now = (clock or (lambda: datetime.now(UTC)))()
    execution.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    await db.flush()
    return execution


async def assert_tool_execution_fence(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Lock and verify an unexpired owner immediately around one side effect."""
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    _require_lease_owner(execution, lease_owner)
    now = (clock or (lambda: datetime.now(UTC)))()
    if execution.lease_expires_at is None or execution.lease_expires_at <= now:
        raise ToolExecutionError(
            "tool_execution_lease_lost",
            "tool execution lease expired before the fenced side effect",
        )
    return execution


async def takeover_tool_execution_for_reconciliation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    lease_ttl_seconds: int,
    reopen_unknown: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ToolExecutionTakeover:
    """Atomically replace only an expired owner before reading durable facts."""
    _require_text(lease_owner, field="lease_owner", max_length=128)
    if lease_ttl_seconds <= 0:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "lease_ttl_seconds must be positive",
        )
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    now = (clock or (lambda: datetime.now(UTC)))()
    if execution.status == "unknown" and reopen_unknown:
        execution.status = "started"
        execution.lease_owner = lease_owner
        execution.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        execution.completed_at = None
        await db.flush()
        return ToolExecutionTakeover(
            execution=execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
        )
    if execution.status != "started":
        if execution.status not in {"succeeded", "failed", "unknown"}:
            raise ToolExecutionError(
                "invalid_tool_execution_state",
                f"tool execution {execution.id} has unsupported status {execution.status}",
            )
        return ToolExecutionTakeover(
            execution=execution,
            acquired=False,
            active=False,
            terminal_outcome=_outcome(execution),
        )

    if execution.lease_owner == lease_owner:
        if execution.lease_expires_at is None or execution.lease_expires_at <= now:
            execution.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
            await db.flush()
        return ToolExecutionTakeover(
            execution=execution,
            acquired=True,
            active=False,
            terminal_outcome=None,
        )
    if execution.lease_expires_at is not None and execution.lease_expires_at > now:
        return ToolExecutionTakeover(
            execution=execution,
            acquired=False,
            active=True,
            terminal_outcome=None,
        )

    execution.lease_owner = lease_owner
    execution.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    await db.flush()
    return ToolExecutionTakeover(
        execution=execution,
        acquired=True,
        active=False,
        terminal_outcome=None,
    )


async def mark_tool_execution_retry_pending(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    error_code: str | None,
    metadata: dict[str, Any] | None = None,
) -> AgentToolExecution:
    """Persist one transient safe-read failure without closing its receipt."""
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    _require_lease_owner(execution, lease_owner)
    effect, retry_policy = _execution_metadata(execution)
    attempt_count = _attempt_count(execution)
    if effect != "read" or retry_policy != "safe":
        raise ToolExecutionError(
            "unsafe_tool_retry",
            "only read tools with retry_policy=safe may remain retry-pending",
        )
    if attempt_count >= SAFE_READ_MAX_ATTEMPTS:
        raise ToolExecutionError(
            "tool_retry_budget_exhausted",
            "safe read tool receipt has no remaining Runtime retry attempts",
        )
    execution.result_summary = result_summary
    execution.result_ref = None
    execution.result_metadata = _bounded_result_metadata(
        {
            **(metadata or {}),
            "error_code": error_code,
            "retryable": True,
            "runtime_attempt_count": attempt_count,
            "runtime_retry_pending": True,
        }
    )
    # The provider has returned a known failure, so no execution remains behind
    # this lease. The next LangGraph attempt must atomically claim the same row.
    execution.lease_owner = None
    execution.lease_expires_at = None
    await db.flush()
    return execution


def _async_operation_key(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    operation = metadata.get("async_operation")
    if not isinstance(operation, dict) or operation.get("version") != 1:
        return None
    value = operation.get("operation_key")
    return value if isinstance(value, str) and value else None


async def mark_tool_execution_async_pending(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    metadata: dict[str, Any],
) -> AgentToolExecution:
    """Persist a declared provider operation without closing or replaying it."""
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    _require_lease_owner(execution, lease_owner)
    if (
        metadata.get("runtime_async_pending") is not True
        or _async_operation_key(metadata) is None
    ):
        raise ToolExecutionError(
            "invalid_async_tool_outcome",
            "pending async outcome requires a stable operation key",
        )
    execution.result_summary = result_summary
    execution.result_ref = None
    execution.result_metadata = _bounded_result_metadata(metadata)
    # The launch/poll request returned and no execution remains behind this
    # lease. A later, separately identified poll call settles the operation.
    execution.lease_owner = None
    execution.lease_expires_at = None
    execution.completed_at = None
    await db.flush()
    return execution


async def settle_async_operation_executions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    status: Literal["succeeded", "failed", "unknown"],
    result_summary: str | None,
    result_ref: str | None,
    error_code: str | None,
    retryable: bool,
    artifact_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    metadata: dict[str, Any],
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Atomically close this poll and same-Run pending receipts for its operation."""
    operation_key = _async_operation_key(metadata)
    if operation_key is None or metadata.get("runtime_async_pending") is not False:
        raise ToolExecutionError(
            "invalid_async_tool_outcome",
            "terminal async outcome requires a stable completed operation key",
        )
    result = await db.execute(
        select(AgentToolExecution)
        .where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.run_id == run_id,
            AgentToolExecution.status == "started",
        )
        .with_for_update()
    )
    pending = list(result.scalars().all())
    completed_at = (clock or (lambda: datetime.now(UTC)))()
    current = await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status=status,
        result_summary=result_summary,
        result_ref=result_ref,
        error_code=error_code,
        retryable=retryable,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        metadata=metadata,
        clock=lambda: completed_at,
    )
    for execution in pending:
        if execution.id == current.id:
            continue
        prior_metadata = (
            execution.result_metadata
            if isinstance(execution.result_metadata, dict)
            else {}
        )
        if (
            prior_metadata.get("runtime_async_pending") is not True
            or _async_operation_key(prior_metadata) != operation_key
        ):
            continue
        execution.status = status
        execution.result_summary = current.result_summary
        execution.result_ref = current.result_ref
        execution.result_metadata = deepcopy(current.result_metadata)
        execution.lease_owner = None
        execution.lease_expires_at = None
        execution.completed_at = completed_at
    await db.flush()
    return current


async def mark_expired_safe_read_result_unavailable(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    probe_error_code: str,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Close an expired safe read only after its result envelope was probed."""
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    if execution.status in {"succeeded", "failed", "unknown"}:
        return execution
    effect, retry_policy = _execution_metadata(execution)
    metadata = (
        execution.result_metadata
        if isinstance(execution.result_metadata, dict)
        else {}
    )
    now = (clock or (lambda: datetime.now(UTC)))()
    if (
        execution.status != "started"
        or effect != "read"
        or retry_policy != "safe"
        or metadata.get("runtime_retry_pending") is True
        or execution.lease_expires_at is None
        or execution.lease_expires_at > now
    ):
        raise ToolExecutionError(
            "safe_read_reconciliation_pending",
            "safe read receipt is not eligible to close after result probing",
        )
    attempt_count = _attempt_count(execution)
    execution.status = "failed"
    execution.result_summary = (
        "The Runtime lost the safe read result before it could record a "
        "durable retryable failure. No recoverable result envelope was found, "
        "so the provider call was not repeated automatically; the model may "
        "make a new decision."
    )
    execution.result_ref = None
    execution.result_metadata = _bounded_result_metadata(
        {
            "error_code": "safe_read_result_unavailable",
            "error_class": probe_error_code,
            "retryable": False,
            "runtime_attempt_count": attempt_count,
            "runtime_retry_pending": False,
        }
    )
    execution.lease_owner = None
    execution.lease_expires_at = None
    execution.completed_at = now
    await db.flush()
    return execution


async def _mark_terminal(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    status: Literal["succeeded", "failed", "unknown"],
    result_summary: str | None,
    result_ref: str | None,
    error_code: str | None,
    retryable: bool,
    artifact_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    metadata: dict[str, Any] | None,
    clock: Callable[[], datetime] | None,
) -> AgentToolExecution:
    if result_summary is not None:
        result_summary, nul_count, control_count, redaction_count = _normalize_text(
            result_summary,
            redact=True,
        )
    else:
        nul_count = control_count = redaction_count = 0
    if result_ref is not None:
        result_ref, ref_nul, ref_control, ref_redactions = _normalize_text(
            result_ref,
            redact=True,
        )
        nul_count += ref_nul
        control_count += ref_control
        redaction_count += ref_redactions
    _require_optional_text(result_ref, field="result_ref", max_length=500)
    if result_summary is not None and len(result_summary) > 1_000_000:
        raise ToolExecutionError(
            "invalid_tool_execution_input",
            "result_summary exceeds its storage limit",
        )
    result_metadata = _bounded_result_metadata(
        {
            **(metadata or {}),
            "error_code": error_code,
            "retryable": retryable,
            "artifact_refs": list(artifact_refs),
            "evidence_refs": list(evidence_refs),
            "nul_replacements": (
                _metadata_count(metadata, "nul_replacements") + nul_count
            ),
            "control_replacements": (
                _metadata_count(metadata, "control_replacements")
                + control_count
            ),
            "redaction_count": (
                _metadata_count(metadata, "redaction_count")
                + redaction_count
            ),
        }
    )
    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    if execution.status == status:
        if (
            execution.result_summary == result_summary
            and execution.result_ref == result_ref
            and (
                not getattr(execution, "result_metadata", None)
                or execution.result_metadata == result_metadata
            )
        ):
            return execution
        raise ToolExecutionError(
            "tool_execution_terminal_conflict",
            "terminal tool execution retry has different outcome data",
        )
    if execution.status in {"succeeded", "failed", "unknown"}:
        raise ToolExecutionError(
            "tool_execution_terminal_conflict",
            f"tool execution is already terminal with status {execution.status}",
        )
    _require_lease_owner(execution, lease_owner)
    execution.status = status
    execution.result_summary = result_summary
    execution.result_ref = result_ref
    execution.result_metadata = result_metadata
    execution.lease_expires_at = None
    execution.completed_at = (clock or (lambda: datetime.now(UTC)))()
    await db.flush()
    return execution


async def mark_tool_execution_succeeded(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None,
    error_code: str | None = None,
    retryable: bool = False,
    artifact_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist a reusable successful receipt under a row lock."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="succeeded",
        result_summary=result_summary,
        result_ref=result_ref,
        error_code=error_code,
        retryable=retryable,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        metadata=metadata,
        clock=clock,
    )


async def mark_tool_execution_failed(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None = None,
    error_code: str | None = None,
    retryable: bool = False,
    artifact_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist a terminal known failure; terminal receipts are never reopened."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="failed",
        result_summary=result_summary,
        result_ref=result_ref,
        error_code=error_code,
        retryable=retryable,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        metadata=metadata,
        clock=clock,
    )


async def mark_tool_execution_unknown(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_owner: str,
    result_summary: str | None,
    result_ref: str | None = None,
    error_code: str | None = None,
    retryable: bool = False,
    artifact_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Persist an uncertain outcome that always requires reconciliation."""
    return await _mark_terminal(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        status="unknown",
        result_summary=result_summary,
        result_ref=result_ref,
        error_code=error_code,
        retryable=retryable,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        metadata=metadata,
        clock=clock,
    )


async def reconcile_unknown_tool_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    confirmed_status: Literal["succeeded", "failed"],
    confirmed_by_user_id: uuid.UUID,
    note: str,
    clock: Callable[[], datetime] | None = None,
) -> AgentToolExecution:
    """Settle one unknown receipt from an explicit, audited human confirmation."""
    normalized_note, _, _, _ = _normalize_text(note, redact=True)
    normalized_note = normalized_note.strip()
    if not normalized_note:
        raise ToolExecutionError(
            "invalid_tool_reconciliation",
            "a reconciliation note is required",
        )
    if len(normalized_note) > 2_000:
        raise ToolExecutionError(
            "invalid_tool_reconciliation",
            "reconciliation note exceeds its storage limit",
        )

    execution = await _get_locked_execution(
        db,
        tenant_id=tenant_id,
        execution_id=execution_id,
    )
    if execution.run_id != run_id:
        raise ToolExecutionError(
            "tool_execution_scope_mismatch",
            "tool execution does not belong to the requested run",
        )
    effect, retry_policy = _execution_metadata(execution)
    if (
        execution.tool_name != "write_file"
        or effect != "write"
        or retry_policy != "conditional"
    ):
        raise ToolExecutionError(
            "tool_execution_reconciliation_not_supported",
            "manual reconciliation is only supported for conditional write_file receipts",
        )

    prior_metadata = (
        execution.result_metadata
        if isinstance(execution.result_metadata, dict)
        else {}
    )
    already_confirmed = prior_metadata.get("external_reconciliation") is True
    if execution.status != "unknown":
        if execution.status == confirmed_status and already_confirmed:
            return execution
        raise ToolExecutionError(
            "tool_execution_reconciliation_conflict",
            f"tool execution cannot be reconciled from status {execution.status}",
        )

    reconciled_at = (clock or (lambda: datetime.now(UTC)))()
    original_completed_at = execution.completed_at
    error_code = (
        "externally_confirmed_applied"
        if confirmed_status == "succeeded"
        else "externally_confirmed_not_applied"
    )
    summary = (
        f"User confirmed that the prior {execution.tool_name} operation took "
        "effect. Do not repeat it."
        if confirmed_status == "succeeded"
        else f"User confirmed that the prior {execution.tool_name} operation "
        "did not take effect. A new tool call may retry it safely."
    )
    execution.status = confirmed_status
    execution.result_summary = summary
    if confirmed_status == "failed":
        execution.result_ref = None
    execution.result_metadata = _bounded_result_metadata(
        {
            **prior_metadata,
            "error_code": error_code,
            "retryable": False,
            "external_reconciliation": True,
            "reconciled_by_user_id": str(confirmed_by_user_id),
            "reconciled_at": reconciled_at.isoformat(),
            "reconciliation_note": normalized_note,
            "original_status": "unknown",
            "original_completed_at": (
                original_completed_at.isoformat()
                if original_completed_at is not None
                else None
            ),
        }
    )
    execution.lease_owner = None
    execution.lease_expires_at = None
    execution.completed_at = reconciled_at
    await db.flush()
    return execution
