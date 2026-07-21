"""Idempotent, caller-transaction delivery of Runtime user-visible messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Literal, cast
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services.chat_session_service import get_primary_direct_session
from app.services.agent_runtime.channel_delivery import stage_channel_delivery
from app.services.agent_runtime.group_handoff import (
    GroupAgentHandoffError,
    apply_group_agent_handoff,
)
from app.services.agent_runtime.state import JsonObject
from app.services.participant_identity import get_or_create_agent_participant


DeliveryKind = Literal["ack", "waiting", "terminal"]
DeliveryLifecycleStatus = Literal[
    "waiting_user",
    "completed",
    "failed",
    "cancelled",
]
OriginalTargetOutcome = Literal["not_attempted", "unknown"]
DeliveryReceiptStatus = Literal["delivered", "failed"]

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TARGET_KINDS = frozenset({"session", "primary_user_session", "direct", "group"})
_BACKGROUND_FALLBACK_KIND = "background"
_PLANNING_ROLE = "group_planning"
_SAFE_CANCELLED = "任务已取消。"


class DeliveryServiceError(RuntimeError):
    """A delivery request or stored receipt violates the Runtime contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        fallback_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.fallback_reason = fallback_reason


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    """A checkpoint-validated user-visible delivery request.

    The caller derives waiting and terminal fields from one authoritative
    checkpoint. This service validates their shape, but intentionally does not
    consult the product projection to decide whether delivery is allowed.
    """

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    kind: DeliveryKind
    content: str
    checkpoint_id: str | None = None
    lifecycle_status: DeliveryLifecycleStatus | None = None
    interrupt_id: str | None = None
    original_target_outcome: OriginalTargetOutcome = "not_attempted"
    group_handoff_intent: JsonObject | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    thinking: str | None = None

    @property
    def idempotency_key(self) -> str:
        if self.kind == "ack":
            return f"run:{self.run_id}:ack"
        if self.kind == "waiting":
            return f"run:{self.run_id}:waiting:{self.interrupt_id}"
        return f"run:{self.run_id}:terminal:{self.lifecycle_status}"


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Stable receipt reconstructed from the delivery event on every retry."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    idempotency_key: str
    status: DeliveryReceiptStatus
    delivery_kind: DeliveryKind
    checkpoint_id: str | None
    message_id: uuid.UUID | None
    requested_session_id: uuid.UUID | None
    actual_session_id: uuid.UUID | None
    fallback_reason: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class _TargetDescriptor:
    kind: str | None
    session_id: uuid.UUID | None
    group_id: uuid.UUID | None
    user_id: uuid.UUID | None

    def payload(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "session_id": str(self.session_id) if self.session_id else None,
            "group_id": str(self.group_id) if self.group_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
        }


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    session: ChatSession
    requested: _TargetDescriptor
    fallback_reason: str | None


def _require_text(
    value: str | None,
    *,
    field: str,
    max_length: int | None = None,
) -> str:
    if value is None or not value.strip():
        raise DeliveryServiceError(
            "invalid_delivery_request",
            f"{field} must be a non-empty string",
        )
    if max_length is not None and len(value) > max_length:
        raise DeliveryServiceError(
            "invalid_delivery_request",
            f"{field} exceeds its {max_length}-character storage limit",
        )
    return value.strip()


def _validate_request(request: DeliveryRequest) -> None:
    if request.kind not in {"ack", "waiting", "terminal"}:
        raise DeliveryServiceError(
            "invalid_delivery_request",
            f"unsupported delivery kind: {request.kind!r}",
        )
    if request.original_target_outcome not in {"not_attempted", "unknown"}:
        raise DeliveryServiceError(
            "invalid_delivery_request",
            "original_target_outcome must be not_attempted or unknown",
        )
    if request.kind == "ack":
        _require_text(request.content, field="content")
        if (
            request.checkpoint_id is not None
            or request.lifecycle_status is not None
            or request.interrupt_id is not None
            or request.group_handoff_intent is not None
            or request.failure_code is not None
            or request.failure_message is not None
        ):
            raise DeliveryServiceError(
                "invalid_delivery_request",
                "ack delivery cannot carry checkpoint lifecycle fields",
            )
        return

    _require_text(request.checkpoint_id, field="checkpoint_id", max_length=255)
    if request.kind == "waiting":
        _require_text(request.content, field="content")
        if request.lifecycle_status != "waiting_user":
            raise DeliveryServiceError(
                "invalid_delivery_request",
                "only waiting_user checkpoints produce a waiting delivery",
            )
        _require_text(request.interrupt_id, field="interrupt_id", max_length=200)
        if request.group_handoff_intent is not None:
            raise DeliveryServiceError(
                "invalid_delivery_request",
                "waiting delivery cannot carry a Group handoff intent",
            )
        if request.failure_code is not None or request.failure_message is not None:
            raise DeliveryServiceError(
                "invalid_delivery_request",
                "waiting delivery cannot carry terminal failure metadata",
            )
        return

    if request.lifecycle_status not in _TERMINAL_STATUSES:
        raise DeliveryServiceError(
            "invalid_delivery_request",
            "terminal delivery requires completed, failed, or cancelled",
        )
    if request.lifecycle_status == "completed":
        _require_text(request.content, field="content")
    if request.interrupt_id is not None:
        raise DeliveryServiceError(
            "invalid_delivery_request",
            "terminal delivery cannot carry an interrupt_id",
        )
    if request.group_handoff_intent is not None:
        if request.lifecycle_status != "completed" or not isinstance(
            request.group_handoff_intent,
            dict,
        ):
            raise DeliveryServiceError(
                "invalid_delivery_request",
                "only completed terminal delivery can carry a Group handoff intent",
            )
    if request.lifecycle_status != "failed" and (
        request.failure_code is not None or request.failure_message is not None
    ):
        raise DeliveryServiceError(
            "invalid_delivery_request",
            "only failed terminal delivery can carry failure metadata",
        )


def _uuid_value(
    target: dict,
    *field_names: str,
) -> uuid.UUID | None:
    values = [target.get(field) for field in field_names if target.get(field) is not None]
    if not values:
        return None
    try:
        resolved = [uuid.UUID(str(value)) for value in values]
    except (TypeError, ValueError) as exc:
        raise DeliveryServiceError(
            "invalid_delivery_target",
            f"{field_names[0]} must be a UUID",
        ) from exc
    if any(value != resolved[0] for value in resolved[1:]):
        raise DeliveryServiceError(
            "invalid_delivery_target",
            f"conflicting {field_names[0]} values",
        )
    return resolved[0]


def _target_descriptor(run: AgentRun) -> _TargetDescriptor:
    raw_target = run.delivery_target or {}
    if not isinstance(raw_target, dict):
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "stored delivery_target must be an object",
        )
    kind = raw_target.get("kind")
    if kind is not None and (not isinstance(kind, str) or kind not in _TARGET_KINDS):
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "stored delivery target kind is unsupported",
        )
    explicit_session_id = _uuid_value(raw_target, "session_id")
    if (
        run.run_kind != _BACKGROUND_FALLBACK_KIND
        and explicit_session_id is not None
        and run.session_id is not None
        and explicit_session_id != run.session_id
    ):
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "foreground and Planning delivery must retain the original session",
        )
    return _TargetDescriptor(
        kind=cast(str | None, kind),
        session_id=explicit_session_id or run.session_id,
        group_id=_uuid_value(raw_target, "group_id", "_origin_group_id"),
        user_id=_uuid_value(
            raw_target,
            "user_id",
            "owner_user_id",
            "_origin_user_id",
        )
        or run.origin_user_id,
    )


async def _load_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.id == session_id,
        )
    )
    return result.scalar_one_or_none()


def _descriptor_with_session_scope(
    descriptor: _TargetDescriptor,
    session: ChatSession,
) -> _TargetDescriptor:
    if descriptor.kind == "group" and session.session_type != "group":
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "group delivery target points to a non-group session",
        )
    if descriptor.kind in {"direct", "primary_user_session"} and session.session_type != "direct":
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "direct delivery target points to a non-direct session",
        )
    if descriptor.group_id is not None and descriptor.group_id != session.group_id:
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "stored group target does not match its requested session",
        )
    if (
        session.session_type == "direct"
        and descriptor.user_id is not None
        and descriptor.user_id != session.user_id
    ):
        raise DeliveryServiceError(
            "invalid_delivery_target",
            "stored user target does not match its requested session",
        )
    return _TargetDescriptor(
        kind=descriptor.kind,
        session_id=descriptor.session_id,
        group_id=descriptor.group_id or session.group_id,
        user_id=descriptor.user_id or session.user_id,
    )


async def _get_primary_group_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == "group",
            ChatSession.group_id == group_id,
            ChatSession.is_primary.is_(True),
            ChatSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _resolve_target(
    db: AsyncSession,
    *,
    run: AgentRun,
    request: DeliveryRequest,
) -> _ResolvedTarget:
    requested = _target_descriptor(run)
    requested_session = None
    unavailable_reason = None
    if requested.session_id is not None:
        requested_session = await _load_session(
            db,
            tenant_id=run.tenant_id,
            session_id=requested.session_id,
        )
        if requested_session is None:
            unavailable_reason = "requested_session_missing"
        else:
            requested = _descriptor_with_session_scope(requested, requested_session)
            if requested_session.deleted_at is None:
                return _ResolvedTarget(
                    session=requested_session,
                    requested=requested,
                    fallback_reason=None,
                )
            unavailable_reason = "requested_session_deleted"

    if run.run_kind != _BACKGROUND_FALLBACK_KIND:
        raise DeliveryServiceError(
            "original_session_unavailable",
            "foreground and Planning delivery cannot switch sessions",
            fallback_reason=unavailable_reason,
        )
    if request.original_target_outcome == "unknown":
        raise DeliveryServiceError(
            "original_target_outcome_unknown",
            "an unknown original write outcome must be reconciled before fallback",
            fallback_reason=unavailable_reason,
        )

    if requested.group_id is not None or requested.kind == "group":
        if requested.group_id is None:
            raise DeliveryServiceError(
                "invalid_delivery_target",
                "group delivery target is missing group_id",
            )
        primary = await _get_primary_group_session(
            db,
            tenant_id=run.tenant_id,
            group_id=requested.group_id,
        )
    else:
        if run.agent_id is None or requested.user_id is None:
            raise DeliveryServiceError(
                "invalid_delivery_target",
                "direct background delivery requires agent_id and user_id",
            )
        primary = await get_primary_direct_session(
            db,
            run.tenant_id,
            run.agent_id,
            requested.user_id,
        )
    if primary is None:
        raise DeliveryServiceError(
            "primary_session_unavailable",
            "no active primary exists in the requested delivery scope",
            fallback_reason=unavailable_reason,
        )
    return _ResolvedTarget(
        session=primary,
        requested=requested,
        fallback_reason=unavailable_reason,
    )


async def _agent_participant(
    db: AsyncSession,
    *,
    run: AgentRun,
) -> Participant | None:
    if run.run_kind == "orchestration":
        if run.agent_id is not None or run.system_role != _PLANNING_ROLE:
            raise DeliveryServiceError(
                "invalid_sender_identity",
                "Planning delivery must use the system sender identity",
            )
        return None
    if run.agent_id is None:
        raise DeliveryServiceError(
            "invalid_sender_identity",
            "non-Planning delivery requires an Agent sender",
        )
    result = await db.execute(
        select(Agent).where(
            Agent.id == run.agent_id,
            Agent.tenant_id == run.tenant_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise DeliveryServiceError(
            "agent_unavailable",
            "delivery Agent does not belong to the Run tenant",
        )
    return await get_or_create_agent_participant(
        db,
        agent.id,
        agent.name,
        agent.avatar_url,
    )


async def _validate_direct_target(
    db: AsyncSession,
    *,
    run: AgentRun,
    session: ChatSession,
) -> None:
    if (
        session.session_type != "direct"
        or session.group_id is not None
        or session.agent_id != run.agent_id
        or session.user_id is None
    ):
        raise DeliveryServiceError(
            "direct_scope_mismatch",
            "direct delivery session does not match the Run Agent scope",
        )
    result = await db.execute(
        select(User).where(
            User.id == session.user_id,
            User.tenant_id == run.tenant_id,
            User.is_active.is_(True),
        )
    )
    if result.scalar_one_or_none() is None:
        raise DeliveryServiceError(
            "delivery_user_unavailable",
            "direct delivery user is not active in the Run tenant",
        )


async def _validate_group_target(
    db: AsyncSession,
    *,
    run: AgentRun,
    session: ChatSession,
    participant: Participant | None,
) -> None:
    if session.session_type != "group":
        raise DeliveryServiceError(
            "group_scope_mismatch",
            "group delivery requires a group session",
        )
    if session.group_id is None:
        if (
            session.external_conv_id is None
            or session.source_channel == "web"
            or session.agent_id != run.agent_id
        ):
            raise DeliveryServiceError(
                "external_group_scope_mismatch",
                "external group delivery does not match the Run Agent and channel scope",
            )
        return
    result = await db.execute(
        select(Group).where(
            Group.id == session.group_id,
            Group.tenant_id == run.tenant_id,
            Group.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise DeliveryServiceError(
            "delivery_group_unavailable",
            "delivery group is missing or deleted",
        )
    if participant is None:
        return
    membership_result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == session.group_id,
            GroupMember.participant_id == participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if membership_result.scalar_one_or_none() is None:
        raise DeliveryServiceError(
            "agent_not_group_member",
            "delivery Agent is not an active member of the target group",
        )


async def _validate_actual_target(
    db: AsyncSession,
    *,
    run: AgentRun,
    resolved: _ResolvedTarget,
) -> Participant | None:
    session = resolved.session
    if session.tenant_id != run.tenant_id or session.deleted_at is not None:
        raise DeliveryServiceError(
            "resolved_session_unavailable",
            "resolved delivery session is unavailable in the Run tenant",
        )
    participant = await _agent_participant(db, run=run)
    if session.session_type == "group":
        await _validate_group_target(
            db,
            run=run,
            session=session,
            participant=participant,
        )
    else:
        if participant is None:
            raise DeliveryServiceError(
                "planning_target_mismatch",
                "Planning delivery requires an original group session",
            )
        await _validate_direct_target(db, run=run, session=session)
    return participant


def _safe_failure_field(value: str | None, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        return fallback
    if not all(character.isalnum() or character in "_-." for character in normalized):
        return fallback
    return normalized


def _safe_failure_content(run: AgentRun, request: DeliveryRequest) -> str:
    code = _safe_failure_field(request.failure_code, fallback="runtime_failed")
    message = (
        request.failure_message.strip()
        if isinstance(request.failure_message, str) and request.failure_message.strip()
        else "后端未提供详细错误信息"
    )
    headline = (
        "任务规划未完成。"
        if run.run_kind == "orchestration" and run.system_role == _PLANNING_ROLE
        else "任务执行未完成。"
    )
    return "\n".join(
        (
            headline,
            f"错误：{message}",
            f"错误码：{code}",
            f"Run ID：{run.id}",
        )
    )


def _safe_message_content(run: AgentRun, request: DeliveryRequest) -> str:
    if request.kind == "terminal" and request.lifecycle_status == "failed":
        return _safe_failure_content(run, request)
    if request.kind == "terminal" and request.lifecycle_status == "cancelled":
        return _SAFE_CANCELLED
    return request.content.strip()


def _message_id(run_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"delivery-message:{idempotency_key}")


def _event_id(run_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"delivery-event:{idempotency_key}")


def _actual_target_payload(session: ChatSession) -> dict[str, str | None]:
    return {
        "session_id": str(session.id),
        "session_type": session.session_type,
        "group_id": str(session.group_id) if session.group_id else None,
        "user_id": str(session.user_id) if session.user_id else None,
    }


def _receipt_payload(receipt: DeliveryReceipt) -> dict[str, object]:
    return {
        "version": 1,
        "status": receipt.status,
        "delivery_kind": receipt.delivery_kind,
        "checkpoint_id": receipt.checkpoint_id,
        "message_id": str(receipt.message_id) if receipt.message_id else None,
        "requested_session_id": (str(receipt.requested_session_id) if receipt.requested_session_id else None),
        "actual_session_id": (str(receipt.actual_session_id) if receipt.actual_session_id else None),
        "fallback_reason": receipt.fallback_reason,
        "error_code": receipt.error_code,
    }


def _receipt_from_event(
    event: AgentRunEvent,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    idempotency_key: str,
) -> DeliveryReceipt:
    payload = event.payload
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise DeliveryServiceError(
            "invalid_delivery_receipt",
            "stored delivery event does not contain a supported receipt",
        )
    try:
        status = payload["status"]
        kind = payload["delivery_kind"]
        message_id = payload.get("message_id")
        requested_session_id = payload.get("requested_session_id")
        actual_session_id = payload.get("actual_session_id")
        if status not in {"delivered", "failed"}:
            raise ValueError("invalid status")
        if kind not in {"ack", "waiting", "terminal"}:
            raise ValueError("invalid delivery kind")
        if event.run_id != run_id or event.tenant_id != tenant_id or event.idempotency_key != idempotency_key:
            raise ValueError("receipt scope mismatch")
        expected_event_type = "delivery_succeeded" if status == "delivered" else "delivery_failed"
        if event.event_type != expected_event_type:
            raise ValueError("receipt event type mismatch")
        return DeliveryReceipt(
            tenant_id=tenant_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            status=cast(DeliveryReceiptStatus, status),
            delivery_kind=cast(DeliveryKind, kind),
            checkpoint_id=cast(str | None, payload.get("checkpoint_id")),
            message_id=uuid.UUID(str(message_id)) if message_id else None,
            requested_session_id=(uuid.UUID(str(requested_session_id)) if requested_session_id else None),
            actual_session_id=(uuid.UUID(str(actual_session_id)) if actual_session_id else None),
            fallback_reason=cast(str | None, payload.get("fallback_reason")),
            error_code=cast(str | None, payload.get("error_code")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliveryServiceError(
            "invalid_delivery_receipt",
            "stored delivery receipt is malformed",
        ) from exc


async def _existing_receipt(
    db: AsyncSession,
    *,
    request: DeliveryRequest,
) -> DeliveryReceipt | None:
    result = await db.execute(
        select(AgentRunEvent).where(
            AgentRunEvent.tenant_id == request.tenant_id,
            AgentRunEvent.run_id == request.run_id,
            AgentRunEvent.idempotency_key == request.idempotency_key,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        return None
    return _receipt_from_event(
        event,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        idempotency_key=request.idempotency_key,
    )


def _add_event(
    db: AsyncSession,
    *,
    run: AgentRun,
    request: DeliveryRequest,
    receipt: DeliveryReceipt,
    requested_target: _TargetDescriptor | None,
    actual_session: ChatSession | None,
    clock: Callable[[], datetime],
) -> None:
    payload = _receipt_payload(receipt)
    payload["lifecycle_status"] = request.lifecycle_status
    payload["correlation_id"] = request.interrupt_id
    payload["requested_target"] = requested_target.payload() if requested_target is not None else None
    payload["actual_target"] = _actual_target_payload(actual_session) if actual_session is not None else None
    payload["group_handoff"] = (
        dict(request.group_handoff_intent)
        if request.group_handoff_intent is not None
        else None
    )
    db.add(
        AgentRunEvent(
            id=_event_id(run.id, request.idempotency_key),
            tenant_id=run.tenant_id,
            run_id=run.id,
            agent_id=run.agent_id,
            event_type=("delivery_succeeded" if receipt.status == "delivered" else "delivery_failed"),
            summary=("Runtime delivery succeeded" if receipt.status == "delivered" else "Runtime delivery failed"),
            payload=payload,
            artifact_refs=[],
            idempotency_key=request.idempotency_key,
            source_checkpoint_id=request.checkpoint_id,
            created_at=clock(),
        )
    )


async def _record_failure(
    db: AsyncSession,
    *,
    run: AgentRun,
    request: DeliveryRequest,
    error_code: str,
    requested_target: _TargetDescriptor | None,
    fallback_reason: str | None,
    clock: Callable[[], datetime],
) -> DeliveryReceipt:
    receipt = DeliveryReceipt(
        tenant_id=run.tenant_id,
        run_id=run.id,
        idempotency_key=request.idempotency_key,
        status="failed",
        delivery_kind=request.kind,
        checkpoint_id=request.checkpoint_id,
        message_id=None,
        requested_session_id=(requested_target.session_id if requested_target is not None else None),
        actual_session_id=None,
        fallback_reason=fallback_reason,
        error_code=error_code,
    )
    run.delivery_status = "failed"
    _add_event(
        db,
        run=run,
        request=request,
        receipt=receipt,
        requested_target=requested_target,
        actual_session=None,
        clock=clock,
    )
    await db.flush()
    return receipt


async def deliver_runtime_message(
    db: AsyncSession,
    request: DeliveryRequest,
    *,
    clock: Callable[[], datetime] | None = None,
) -> DeliveryReceipt:
    """Deliver one ACK, waiting prompt, or terminal result without committing.

    Locking the Run row serializes concurrent attempts until the caller commits.
    The deterministic ChatMessage UUID and the delivery event payload form the
    receipt binding required by the current schema.
    """

    _validate_request(request)
    now = clock or (lambda: datetime.now(UTC))
    run_result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == request.tenant_id,
            AgentRun.id == request.run_id,
        )
        .with_for_update()
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise DeliveryServiceError(
            "run_not_found",
            "delivery Run does not exist in the requested tenant",
        )
    if run.tenant_id != request.tenant_id or run.id != request.run_id:
        raise DeliveryServiceError(
            "run_scope_mismatch",
            "loaded delivery Run is outside the requested tenant scope",
        )
    if (
        run.runtime_type != "langgraph"
        or not run.runtime_thread_id
        or not run.runtime_thread_id.strip()
    ):
        raise DeliveryServiceError(
            "runtime_identity_mismatch",
            "delivery requires a LangGraph Run with a non-empty thread_id",
        )

    existing = await _existing_receipt(db, request=request)
    if existing is not None:
        return existing

    try:
        requested_target = _target_descriptor(run)
        resolved = await _resolve_target(db, run=run, request=request)
        participant = await _validate_actual_target(
            db,
            run=run,
            resolved=resolved,
        )
    except DeliveryServiceError as exc:
        target = None
        try:
            target = _target_descriptor(run)
        except DeliveryServiceError:
            pass
        return await _record_failure(
            db,
            run=run,
            request=request,
            error_code=exc.code,
            requested_target=target,
            fallback_reason=exc.fallback_reason,
            clock=now,
        )

    message_id = _message_id(run.id, request.idempotency_key)
    session = resolved.session
    if request.group_handoff_intent is not None:
        if (
            session.session_type != "group"
            or session.group_id is None
            or participant is None
        ):
            return await _record_failure(
                db,
                run=run,
                request=request,
                error_code="group_handoff_scope_invalid",
                requested_target=requested_target,
                fallback_reason=resolved.fallback_reason,
                clock=now,
            )
        try:
            applied = await apply_group_agent_handoff(
                db,
                source_run=run,
                content=_safe_message_content(run, request),
                intent_payload=request.group_handoff_intent,
                expected_idempotency_key=request.idempotency_key,
                expected_message_id=message_id,
                clock=now,
            )
        except GroupAgentHandoffError as exc:
            if not exc.repairable:
                raise
            return await _record_failure(
                db,
                run=run,
                request=request,
                error_code=exc.code,
                requested_target=requested_target,
                fallback_reason=resolved.fallback_reason,
                clock=now,
            )
        message = applied.message
    else:
        message = ChatMessage(
            id=message_id,
            agent_id=run.agent_id,
            user_id=session.user_id if session.session_type == "direct" else None,
            role="system" if participant is None else "assistant",
            content=_safe_message_content(run, request),
            thinking=request.thinking if session.session_type == "direct" else None,
            conversation_id=str(session.id),
            participant_id=participant.id if participant is not None else None,
            mentions=[],
            created_at=now(),
        )
        db.add(message)
        session.last_message_at = now()
    channel_delivery = stage_channel_delivery(
        db,
        run=run,
        session=session,
        message_id=message.id,
        idempotency_key=request.idempotency_key,
        clock=now,
    )
    receipt = DeliveryReceipt(
        tenant_id=run.tenant_id,
        run_id=run.id,
        idempotency_key=request.idempotency_key,
        status="delivered",
        delivery_kind=request.kind,
        checkpoint_id=request.checkpoint_id,
        message_id=message.id,
        requested_session_id=requested_target.session_id,
        actual_session_id=session.id,
        fallback_reason=resolved.fallback_reason,
        error_code=None,
    )
    run.delivery_status = "pending" if channel_delivery is not None else "delivered"
    _add_event(
        db,
        run=run,
        request=request,
        receipt=receipt,
        requested_target=requested_target,
        actual_session=session,
        clock=now,
    )
    await db.flush()
    return receipt


__all__ = [
    "DeliveryRequest",
    "DeliveryReceipt",
    "DeliveryServiceError",
    "deliver_runtime_message",
]
