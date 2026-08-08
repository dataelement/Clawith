"""Durable external-channel delivery without re-running the Agent Graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Callable, Literal, Protocol
import uuid

from sqlalchemy import and_, or_, select

from app.config import Settings, get_settings
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.models.channel_delivery import ChannelDelivery
from app.models.chat_session import ChatSession
from app.services.agent_runtime.command_worker import RuntimeSessionFactory


ChannelDeliveryWorkStatus = Literal["idle", "delivered", "retry", "failed"]
_SUPPORTED_CHANNELS = frozenset(
    {
        "feishu",
        "dingtalk",
        "wecom",
        "wechat",
        "whatsapp",
        "slack",
        "discord",
        "microsoft_teams",
    }
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


class ChannelDeliveryError(RuntimeError):
    """A persisted channel route or outbox transition is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChannelDeliveryEnvelope:
    delivery_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID
    channel: str
    target: dict
    content: str
    idempotency_key: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ChannelSendResult:
    provider_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelDeliveryWorkResult:
    status: ChannelDeliveryWorkStatus
    delivery_id: uuid.UUID | None = None
    attempt_count: int = 0
    error_code: str | None = None


class ChannelDeliverySender(Protocol):
    async def send(self, envelope: ChannelDeliveryEnvelope) -> ChannelSendResult:
        """Send one claimed envelope or raise when the provider did not confirm it."""


def _delivery_id(run_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"channel-delivery:{idempotency_key}")


def _event_id(delivery_id: uuid.UUID, outcome: str) -> uuid.UUID:
    return uuid.uuid5(delivery_id, f"channel-delivery-event:{outcome}")


def build_channel_delivery_route(channel: str, target: dict) -> dict:
    """Validate provider routing metadata before it is persisted on a Run."""
    normalized_channel = channel.strip()
    if normalized_channel not in _SUPPORTED_CHANNELS:
        raise ChannelDeliveryError(
            "channel_provider_unsupported",
            f"Unsupported external channel: {normalized_channel or '<blank>'}",
        )
    if not isinstance(target, dict) or not target:
        raise ChannelDeliveryError(
            "invalid_channel_delivery_route",
            "External channel delivery target must be a non-empty object",
        )
    return {
        "version": 1,
        "channel": normalized_channel,
        "target": dict(target),
    }


def _route(run: AgentRun, session: ChatSession) -> tuple[str, dict] | None:
    raw_delivery_target = run.delivery_target or {}
    if not isinstance(raw_delivery_target, dict):
        raise ChannelDeliveryError(
            "invalid_channel_delivery_route",
            "Run delivery_target must be an object",
        )
    raw_route = raw_delivery_target.get("channel_delivery")
    if raw_route is None:
        return None
    if not isinstance(raw_route, dict) or raw_route.get("version") != 1:
        raise ChannelDeliveryError(
            "invalid_channel_delivery_route",
            "channel_delivery must be a version 1 object",
        )
    channel = raw_route.get("channel")
    target = raw_route.get("target")
    if channel not in _SUPPORTED_CHANNELS or not isinstance(target, dict) or not target:
        raise ChannelDeliveryError(
            "invalid_channel_delivery_route",
            "channel_delivery requires a supported channel and non-empty target",
        )
    if session.source_channel != channel:
        raise ChannelDeliveryError(
            "channel_delivery_scope_mismatch",
            "channel delivery route does not match the resolved session channel",
        )
    if run.agent_id is None:
        raise ChannelDeliveryError(
            "channel_delivery_agent_missing",
            "external channel delivery requires an Agent sender",
        )
    return channel, dict(target)


def stage_channel_delivery(
    db,
    *,
    run: AgentRun,
    session: ChatSession,
    message_id: uuid.UUID,
    idempotency_key: str,
    clock: Callable[[], datetime],
) -> ChannelDelivery | None:
    """Add one provider outbox row to the caller's ChatMessage transaction."""
    route = _route(run, session)
    if route is None:
        return None
    channel, target = route
    delivery = ChannelDelivery(
        id=_delivery_id(run.id, idempotency_key),
        tenant_id=run.tenant_id,
        run_id=run.id,
        agent_id=run.agent_id,
        session_id=session.id,
        message_id=message_id,
        channel=channel,
        target=target,
        idempotency_key=idempotency_key,
        status="pending",
        attempt_count=0,
        next_attempt_at=clock(),
        created_at=clock(),
        updated_at=clock(),
    )
    db.add(delivery)
    return delivery


def _safe_error(exc: Exception) -> tuple[str, str]:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code.strip():
        code = type(exc).__name__ or "channel_send_failed"
    message = _BEARER_RE.sub("Bearer [redacted]", str(exc))
    message = _URL_RE.sub("[url]", message).strip()
    if not message:
        message = "External channel provider did not confirm delivery"
    return code[:100], message[:1000]


def _backoff(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(2 ** max(attempt_count - 1, 0), 300))


class ChannelDeliveryWorker:
    """Claim and send outbox rows; failures never invoke or resume the Graph."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        sender: ChannelDeliverySender,
        claimant: str,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_claimant = claimant.strip()
        if not normalized_claimant or len(normalized_claimant) > 128:
            raise ValueError("channel delivery claimant must be 1-128 characters")
        self._session_factory = session_factory
        self._sender = sender
        self._claimant = normalized_claimant
        self._settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _claim(self) -> ChannelDeliveryEnvelope | None:
        now = self._clock()
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChannelDelivery)
                .where(
                    or_(
                        and_(
                            ChannelDelivery.status == "pending",
                            ChannelDelivery.next_attempt_at <= now,
                        ),
                        and_(
                            ChannelDelivery.status == "claimed",
                            ChannelDelivery.claim_expires_at <= now,
                        ),
                    )
                )
                .order_by(ChannelDelivery.next_attempt_at, ChannelDelivery.created_at, ChannelDelivery.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            delivery = result.scalar_one_or_none()
            if delivery is None:
                return None
            message_result = await db.execute(
                select(ChatMessage).where(
                    ChatMessage.id == delivery.message_id,
                    ChatMessage.conversation_id == str(delivery.session_id),
                )
            )
            message = message_result.scalar_one_or_none()
            if message is None or message.role not in {"assistant", "system"}:
                delivery.status = "failed"
                delivery.last_error_code = "channel_message_missing"
                delivery.last_error = "Persisted channel delivery message is missing"
                delivery.claimed_by = None
                delivery.claim_expires_at = None
                await self._set_latest_run_status(db, delivery=delivery, status="failed")
                self._add_outcome_event(db, delivery=delivery, outcome="failed")
                await db.commit()
                return None

            delivery.status = "claimed"
            delivery.claimed_by = self._claimant
            delivery.claim_expires_at = now + timedelta(
                seconds=self._settings.AGENT_RUNTIME_CHANNEL_DELIVERY_CLAIM_TTL_SECONDS
            )
            delivery.attempt_count += 1
            delivery.updated_at = now
            await db.commit()
            return ChannelDeliveryEnvelope(
                delivery_id=delivery.id,
                tenant_id=delivery.tenant_id,
                run_id=delivery.run_id,
                agent_id=delivery.agent_id,
                session_id=delivery.session_id,
                message_id=delivery.message_id,
                channel=delivery.channel,
                target=dict(delivery.target),
                content=message.content,
                idempotency_key=delivery.idempotency_key,
                attempt_count=delivery.attempt_count,
            )

    async def _locked_claim(self, db, delivery_id: uuid.UUID) -> ChannelDelivery:
        result = await db.execute(
            select(ChannelDelivery)
            .where(ChannelDelivery.id == delivery_id)
            .with_for_update()
        )
        delivery = result.scalar_one_or_none()
        if delivery is None:
            raise ChannelDeliveryError(
                "channel_delivery_missing",
                "Claimed channel delivery no longer exists",
            )
        if delivery.status != "claimed" or delivery.claimed_by != self._claimant:
            raise ChannelDeliveryError(
                "channel_delivery_claim_lost",
                "Channel delivery claim is no longer owned by this worker",
            )
        return delivery

    async def _set_latest_run_status(self, db, *, delivery: ChannelDelivery, status: str) -> None:
        result = await db.execute(
            select(ChannelDelivery.id)
            .where(ChannelDelivery.run_id == delivery.run_id)
            .order_by(ChannelDelivery.created_at.desc(), ChannelDelivery.id.desc())
            .limit(1)
        )
        if result.scalar_one_or_none() != delivery.id:
            return
        run_result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.tenant_id == delivery.tenant_id,
                AgentRun.id == delivery.run_id,
            )
            .with_for_update()
        )
        run = run_result.scalar_one_or_none()
        if run is not None:
            run.delivery_status = status

    def _add_outcome_event(
        self,
        db,
        *,
        delivery: ChannelDelivery,
        outcome: Literal["delivered", "failed"],
    ) -> None:
        db.add(
            AgentRunEvent(
                id=_event_id(delivery.id, outcome),
                tenant_id=delivery.tenant_id,
                run_id=delivery.run_id,
                agent_id=delivery.agent_id,
                event_type=f"channel_delivery_{outcome}",
                summary=f"External channel delivery {outcome}",
                payload={
                    "version": 1,
                    "channel_delivery_id": str(delivery.id),
                    "message_id": str(delivery.message_id),
                    "channel": delivery.channel,
                    "attempt_count": delivery.attempt_count,
                    "provider_message_id": delivery.provider_message_id,
                    "error_code": delivery.last_error_code,
                },
                artifact_refs=[],
                idempotency_key=f"channel-delivery:{delivery.id}:{outcome}",
                source_checkpoint_id=None,
                created_at=self._clock(),
            )
        )

    async def _complete(
        self,
        envelope: ChannelDeliveryEnvelope,
        result: ChannelSendResult,
    ) -> ChannelDeliveryWorkResult:
        now = self._clock()
        async with self._session_factory() as db:
            delivery = await self._locked_claim(db, envelope.delivery_id)
            delivery.status = "delivered"
            delivery.provider_message_id = (
                result.provider_message_id[:500]
                if result.provider_message_id
                else None
            )
            delivery.last_error_code = None
            delivery.last_error = None
            delivery.claimed_by = None
            delivery.claim_expires_at = None
            delivery.delivered_at = now
            delivery.updated_at = now
            await self._set_latest_run_status(db, delivery=delivery, status="delivered")
            self._add_outcome_event(db, delivery=delivery, outcome="delivered")
            await db.commit()
        return ChannelDeliveryWorkResult(
            status="delivered",
            delivery_id=envelope.delivery_id,
            attempt_count=envelope.attempt_count,
        )

    async def _fail(
        self,
        envelope: ChannelDeliveryEnvelope,
        exc: Exception,
    ) -> ChannelDeliveryWorkResult:
        now = self._clock()
        error_code, error = _safe_error(exc)
        terminal = (
            envelope.attempt_count
            >= self._settings.AGENT_RUNTIME_CHANNEL_DELIVERY_MAX_ATTEMPTS
        )
        async with self._session_factory() as db:
            delivery = await self._locked_claim(db, envelope.delivery_id)
            delivery.status = "failed" if terminal else "pending"
            delivery.last_error_code = error_code
            delivery.last_error = error
            delivery.claimed_by = None
            delivery.claim_expires_at = None
            delivery.next_attempt_at = now + _backoff(envelope.attempt_count)
            delivery.updated_at = now
            if terminal:
                await self._set_latest_run_status(db, delivery=delivery, status="failed")
                self._add_outcome_event(db, delivery=delivery, outcome="failed")
            await db.commit()
        return ChannelDeliveryWorkResult(
            status="failed" if terminal else "retry",
            delivery_id=envelope.delivery_id,
            attempt_count=envelope.attempt_count,
            error_code=error_code,
        )

    async def run_once(self) -> ChannelDeliveryWorkResult:
        envelope = await self._claim()
        if envelope is None:
            return ChannelDeliveryWorkResult(status="idle")
        try:
            result = await self._sender.send(envelope)
        except Exception as exc:
            return await self._fail(envelope, exc)
        return await self._complete(envelope, result)


__all__ = [
    "ChannelDeliveryEnvelope",
    "ChannelDeliveryError",
    "ChannelDeliverySender",
    "ChannelDeliveryWorkResult",
    "ChannelDeliveryWorker",
    "ChannelSendResult",
    "build_channel_delivery_route",
    "stage_channel_delivery",
]
