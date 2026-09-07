"""Non-blocking Audit observation and administrator read contracts."""

import asyncio
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol, TypeAlias, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import InvalidInput
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.audit.models import AuditRecord
from app.modules.audit.repository import AuditRepository
from app.modules.identity_tenant.public import TenantPrincipal, require_admin

AuditOutcome = Literal["succeeded", "failed", "denied"]
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

MAX_METADATA_BYTES = 8192
MAX_METADATA_DEPTH = 8
MAX_METADATA_ITEMS = 100
METADATA_SCHEMA_VERSION = 1
MAX_PAGE_SIZE = 100
SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "encrypted_payload",
        "password",
        "refresh_token",
        "access_token",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class MembershipActor:
    membership_id: UUID


@dataclass(frozen=True, slots=True)
class PlatformAccountActor:
    account_id: UUID


@dataclass(frozen=True, slots=True)
class AgentActor:
    agent_id: UUID
    run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SystemActor:
    component: str


AuditActor: TypeAlias = MembershipActor | PlatformAccountActor | AgentActor | SystemActor


@dataclass(frozen=True, slots=True)
class AuditRecordView:
    id: UUID
    tenant_id: UUID
    actor: AuditActor
    action: str
    target_kind: str
    target_reference: str
    outcome: AuditOutcome
    metadata_schema_version: int
    metadata: Mapping[str, JSONValue]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AuditObservation:
    tenant_id: UUID
    actor: AuditActor
    action: str
    target_kind: str
    target_reference: str
    outcome: AuditOutcome
    metadata_schema_version: int
    metadata: Mapping[str, JSONValue]
    occurred_at: datetime


class AuditSink(Protocol):
    """Emit committed observations without I/O or propagating Audit failures."""

    def emit(self, observation: AuditObservation) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditStatistics:
    accepted: int = 0
    persisted: int = 0
    dropped_invalid: int = 0
    dropped_full: int = 0
    dropped_closed: int = 0
    write_failed: int = 0
    dropped_shutdown: int = 0


@dataclass(frozen=True, slots=True)
class _PendingObservation:
    observation: AuditObservation
    metadata_json: str


class AsyncAuditSink:
    """Application-owned, event-loop-local, bounded best-effort Audit consumer.

    Start once, emit after business commit, then close before disposing sessions.
    Counters contain no caller values; full, invalid and failed writes are dropped.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        capacity: int,
        shutdown_timeout: float,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Audit capacity must be a positive integer")
        if not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0:
            raise ValueError("Audit shutdown timeout must be finite and positive")
        self._sessions = sessions
        self._queue: asyncio.Queue[_PendingObservation] = asyncio.Queue(capacity)
        self._shutdown_timeout = shutdown_timeout
        self._task: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._closed = False
        self._statistics = AuditStatistics()

    @property
    def statistics(self) -> AuditStatistics:
        return self._statistics

    def _count(self, field: str, amount: int = 1) -> None:
        # Saturation keeps both storage and diagnostic cardinality bounded.
        value = min(getattr(self._statistics, field) + amount, 2**63 - 1)
        self._statistics = replace(self._statistics, **{field: value})

    def start(self) -> None:
        if self._task is not None or self._closed:
            raise RuntimeError("Audit consumer can only be started once")
        self._task = asyncio.create_task(self._consume(), name="audit-observation-consumer")

    def emit(self, observation: AuditObservation) -> None:
        if self._closed or self._task is None or self._task.done():
            self._count("dropped_closed")
            return
        if self._queue.full():
            self._count("dropped_full")
            return
        try:
            pending = _prepare_observation(observation)
        except Exception:  # noqa: BLE001 - Audit observation errors cannot affect producers.
            # Only observation preparation is optional; never log input or exception text.
            self._count("dropped_invalid")
            return
        self._queue.put_nowait(pending)
        self._count("accepted")

    async def _consume(self) -> None:
        while True:
            pending = await self._queue.get()
            try:
                await self._persist(pending)
            except asyncio.CancelledError:
                self._count("dropped_shutdown")
                raise
            except Exception:  # noqa: BLE001 - Isolate this optional storage operation.
                # Contain this one Audit write, including session/transaction failures.
                self._count("write_failed")
            else:
                self._count("persisted")
            finally:
                self._queue.task_done()

    async def _persist(self, pending: _PendingObservation) -> None:
        observation = pending.observation
        async with transaction(self._sessions) as tx:
            repository = AuditRepository(tx.session)
            repository.add(
                AuditRecord(
                    id=uuid4(),
                    tenant_id=observation.tenant_id,
                    action=observation.action,
                    target_kind=observation.target_kind,
                    target_reference=observation.target_reference,
                    outcome=observation.outcome,
                    metadata_schema_version=observation.metadata_schema_version,
                    metadata_payload=json.loads(pending.metadata_json),
                    occurred_at=observation.occurred_at,
                    **_actor_fields(observation.actor),
                )
            )
            await repository.flush()

    async def close(self) -> None:
        self._closed = True
        if self._closing is None:
            self._closing = asyncio.create_task(self._close(), name="audit-observation-close")
        try:
            await asyncio.shield(self._closing)
        except asyncio.CancelledError:
            # Finish bounded draining and connection cleanup before returning cancellation.
            await self._closing
            raise

    async def _close(self) -> None:
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=self._shutdown_timeout)
        except TimeoutError:
            pass
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
                self._count("dropped_shutdown")


def _prepare_observation(observation: AuditObservation) -> _PendingObservation:
    if not isinstance(observation.tenant_id, UUID):
        raise InvalidInput("Audit tenant must be a UUID")
    if observation.outcome not in {"succeeded", "failed", "denied"}:
        raise InvalidInput("unsupported Audit outcome")
    if observation.occurred_at.tzinfo is None or observation.occurred_at.utcoffset() is None:
        raise InvalidInput("Audit occurrence time must be timezone-aware")
    _validate_metadata_schema_version(observation.metadata_schema_version)
    _actor_fields(observation.actor)
    metadata = _validate_metadata(observation.metadata)
    detached = replace(
        observation,
        action=_required_text(observation.action, field_name="action", max_length=128),
        target_kind=_required_text(observation.target_kind, field_name="target kind", max_length=128),
        target_reference=_required_text(observation.target_reference, field_name="target reference", max_length=512),
        metadata={},
    )
    return _PendingObservation(detached, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))


class AuditService:
    """Tenant-administrator reads; writes are private to the asynchronous consumer."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = AuditRepository(transaction.session)

    async def list(
        self,
        principal: TenantPrincipal,
        *,
        limit: int = MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AuditRecordView, ...]:
        require_admin(principal)
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidInput(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise InvalidInput("offset must be non-negative")
        records = await self._repository.list(principal.tenant_id, limit=limit, offset=offset)
        return tuple(_record_view(record) for record in records)


def _actor_fields(actor: AuditActor) -> dict[str, object]:
    fields: dict[str, object] = {
        "membership_id": None,
        "platform_account_id": None,
        "agent_id": None,
        "run_id": None,
        "system_component": None,
    }
    if isinstance(actor, MembershipActor):
        fields.update(actor_kind="membership", membership_id=actor.membership_id)
    elif isinstance(actor, PlatformAccountActor):
        fields.update(actor_kind="platform_account", platform_account_id=actor.account_id)
    elif isinstance(actor, AgentActor):
        fields.update(actor_kind="agent", agent_id=actor.agent_id, run_id=actor.run_id)
    elif isinstance(actor, SystemActor):
        fields.update(
            actor_kind="system",
            system_component=_required_text(actor.component, field_name="system component", max_length=128),
        )
    else:
        raise InvalidInput("unsupported Audit actor")
    return fields


def _validate_metadata(metadata: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    item_count = [0]
    validated = _validate_json_object(metadata, depth=1, item_count=item_count)
    try:
        encoded = json.dumps(
            validated,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise InvalidInput("metadata must contain finite JSON values") from None
    if len(encoded) > MAX_METADATA_BYTES:
        raise InvalidInput(f"metadata must not exceed {MAX_METADATA_BYTES} bytes")
    return validated


def _validate_json_object(value: Mapping[str, JSONValue], *, depth: int, item_count: list[int]) -> dict[str, JSONValue]:
    if depth > MAX_METADATA_DEPTH:
        raise InvalidInput(f"metadata must not exceed {MAX_METADATA_DEPTH} levels")
    result: dict[str, JSONValue] = {}
    for key, nested in value.items():
        if not isinstance(key, str) or not key or len(key) > MAX_METADATA_BYTES:
            raise InvalidInput("metadata object keys must be non-empty strings")
        _reject_secret_field(key)
        item_count[0] += 1
        _check_item_count(item_count[0])
        result[key] = _validate_json_value(nested, depth=depth + 1, item_count=item_count)
    return result


def _validate_json_value(value: JSONValue, *, depth: int, item_count: list[int]) -> JSONValue:
    if isinstance(value, Mapping):
        return _validate_json_object(value, depth=depth, item_count=item_count)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if depth > MAX_METADATA_DEPTH:
            raise InvalidInput(f"metadata must not exceed {MAX_METADATA_DEPTH} levels")
        result: list[JSONValue] = []
        for nested in value:
            item_count[0] += 1
            _check_item_count(item_count[0])
            result.append(
                _validate_json_value(
                    cast(JSONValue, nested),
                    depth=depth + 1,
                    item_count=item_count,
                )
            )
        return result
    if isinstance(value, str) and len(value) > MAX_METADATA_BYTES:
        raise InvalidInput("metadata string exceeds byte limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InvalidInput("metadata must contain finite JSON values")


def _reject_secret_field(key: str) -> None:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized in SECRET_FIELD_NAMES or normalized.endswith(("_password", "_secret", "_token", "_api_key")):
        raise InvalidInput("metadata must not contain Secret fields")


def _check_item_count(item_count: int) -> None:
    if item_count > MAX_METADATA_ITEMS:
        raise InvalidInput(f"metadata must not contain more than {MAX_METADATA_ITEMS} items")


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    if len(value) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    return normalized


def _record_view(record: AuditRecord) -> AuditRecordView:
    _validate_metadata_schema_version(record.metadata_schema_version)
    actor: AuditActor
    if record.actor_kind == "membership":
        actor = MembershipActor(cast(UUID, record.membership_id))
    elif record.actor_kind == "platform_account":
        actor = PlatformAccountActor(cast(UUID, record.platform_account_id))
    elif record.actor_kind == "agent":
        actor = AgentActor(cast(UUID, record.agent_id), record.run_id)
    else:
        actor = SystemActor(cast(str, record.system_component))
    return AuditRecordView(
        id=record.id,
        tenant_id=record.tenant_id,
        actor=actor,
        action=record.action,
        target_kind=record.target_kind,
        target_reference=record.target_reference,
        outcome=cast(AuditOutcome, record.outcome),
        metadata_schema_version=record.metadata_schema_version,
        metadata=_validate_metadata(cast(Mapping[str, JSONValue], record.metadata_payload)),
        occurred_at=record.occurred_at,
    )


def _validate_metadata_schema_version(version: int) -> None:
    if version != METADATA_SCHEMA_VERSION:
        raise InvalidInput("unsupported Audit metadata schema version")
