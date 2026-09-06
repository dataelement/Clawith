"""Public append-only Audit contracts."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeAlias, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import InvalidInput
from app.infrastructure.transactions import TransactionContext
from app.modules.audit.models import AuditRecord
from app.modules.audit.repository import AuditRepository
from app.modules.identity_tenant.public import TenantPrincipal, require_admin

AuditOutcome = Literal["succeeded", "failed", "denied"]
JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)

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


AuditActor: TypeAlias = (
    MembershipActor | PlatformAccountActor | AgentActor | SystemActor
)


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


class AuditService:
    """Append and query Audit facts within a caller-owned transaction."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = AuditRepository(transaction.session)

    async def append(
        self,
        *,
        tenant_id: UUID,
        actor: AuditActor,
        action: str,
        target_kind: str,
        target_reference: str,
        outcome: AuditOutcome,
        metadata_schema_version: int,
        metadata: Mapping[str, JSONValue],
    ) -> AuditRecordView:
        if outcome not in {"succeeded", "failed", "denied"}:
            raise InvalidInput("outcome must be succeeded, failed, or denied")
        _validate_metadata_schema_version(metadata_schema_version)
        validated_metadata = _validate_metadata(metadata)
        actor_fields = _actor_fields(actor)
        record = AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            action=_required_text(action, field_name="action", max_length=128),
            target_kind=_required_text(
                target_kind, field_name="target_kind", max_length=128
            ),
            target_reference=_required_text(
                target_reference, field_name="target_reference", max_length=512
            ),
            outcome=outcome,
            metadata_schema_version=metadata_schema_version,
            metadata_payload=validated_metadata,
            occurred_at=datetime.now(UTC),
            **actor_fields,
        )
        self._repository.add(record)
        try:
            await self._repository.flush()
        except IntegrityError:
            raise InvalidInput(
                "Audit actor does not match the target Tenant or Run"
            ) from None
        return _record_view(record)

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
        records = await self._repository.list(
            principal.tenant_id, limit=limit, offset=offset
        )
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
        fields.update(
            actor_kind="platform_account", platform_account_id=actor.account_id
        )
    elif isinstance(actor, AgentActor):
        fields.update(actor_kind="agent", agent_id=actor.agent_id, run_id=actor.run_id)
    elif isinstance(actor, SystemActor):
        fields.update(
            actor_kind="system",
            system_component=_required_text(
                actor.component, field_name="system component", max_length=128
            ),
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


def _validate_json_object(
    value: Mapping[str, JSONValue], *, depth: int, item_count: list[int]
) -> dict[str, JSONValue]:
    if depth > MAX_METADATA_DEPTH:
        raise InvalidInput(f"metadata must not exceed {MAX_METADATA_DEPTH} levels")
    result: dict[str, JSONValue] = {}
    for key, nested in value.items():
        if not isinstance(key, str) or not key:
            raise InvalidInput("metadata object keys must be non-empty strings")
        _reject_secret_field(key)
        item_count[0] += 1
        _check_item_count(item_count[0])
        result[key] = _validate_json_value(
            nested, depth=depth + 1, item_count=item_count
        )
    return result


def _validate_json_value(
    value: JSONValue, *, depth: int, item_count: list[int]
) -> JSONValue:
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
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InvalidInput("metadata must contain finite JSON values")


def _reject_secret_field(key: str) -> None:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized in SECRET_FIELD_NAMES or normalized.endswith(
        ("_password", "_secret", "_token", "_api_key")
    ):
        raise InvalidInput("metadata must not contain Secret fields")


def _check_item_count(item_count: int) -> None:
    if item_count > MAX_METADATA_ITEMS:
        raise InvalidInput(f"metadata must not contain more than {MAX_METADATA_ITEMS} items")


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
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
        metadata=_validate_metadata(
            cast(Mapping[str, JSONValue], record.metadata_payload)
        ),
        occurred_at=record.occurred_at,
    )


def _validate_metadata_schema_version(version: int) -> None:
    if version != METADATA_SCHEMA_VERSION:
        raise InvalidInput("unsupported Audit metadata schema version")
