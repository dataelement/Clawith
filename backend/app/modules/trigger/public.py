"""Explicit schedule/event triggers and their durable, idempotent occurrences."""

import builtins
import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text, cast, func, or_, select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.credential.public import CredentialService
from app.modules.group.public import GroupService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.run.public import (
    InputContent,
    InputReference,
    RunService,
    RunView,
    SourceIdentity,
    TerminalOutcomePayload,
)
from app.modules.session.public import SessionService
from app.modules.tool.public import AgentToolResolutionScope, EnabledSources, ToolResolutionScope, ToolService
from app.modules.trigger.models import AgentTriggerRecord, TriggerOccurrenceRecord
from app.modules.workspace.public import WorkspaceSubject

TriggerKind = Literal["cron", "once", "interval", "poll", "on_message", "webhook"]


def _validate_destination(kind: object, identity: object, conversation: object) -> None:
    if (kind not in (None, "session", "group") or (kind is None) != (identity is None)
            or (identity is not None and not isinstance(identity, UUID))
            or (conversation is not None and (kind != "group" or not isinstance(conversation, UUID)))):
        raise InvalidInput("Scheduled destination must identify one explicit Session or Group")


_DESTINATION_FIELDS = {"destination_kind", "destination_id", "destination_conversation_id"}
_ORIGIN_FIELDS = {"origin_kind", "origin_id", "origin_conversation_id"}


def _read_origin(payload: dict[str, object]) -> tuple[Literal["agent", "membership", "group"], UUID, UUID | None]:
    kind, identity, conversation = TypeAdapter(tuple[Literal["agent", "membership", "group"], UUID, UUID | None]).validate_json(
        json.dumps([payload["origin_kind"], payload["origin_id"], payload["origin_conversation_id"]]), strict=True)
    if (kind == "group") != (conversation is not None):
        raise InvalidInput("Scheduled origin requires its exact Group conversation")
    return kind, identity, conversation


def _read_destination(payload: dict[str, object]) -> tuple[Literal["session", "group"] | None, UUID | None, UUID | None]:
    values = TypeAdapter(tuple[Literal["session", "group"] | None, UUID | None, UUID | None]).validate_json(
        json.dumps([payload.get("destination_kind"), payload.get("destination_id"), payload.get("destination_conversation_id")]), strict=True)
    _validate_destination(*values)
    return values


def _destination_payload(config: "TriggerConfig") -> dict[str, object]:
    return {"destination_kind": config.destination_kind,
        "destination_id": str(config.destination_id) if config.destination_id is not None else None,
        "destination_conversation_id": str(config.destination_conversation_id) if config.destination_conversation_id is not None else None}


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    name: str
    kind: TriggerKind
    instruction: str
    timezone: str = "UTC"
    cron_expression: str | None = None
    at: datetime | None = None
    interval_minutes: int | None = None
    poll_url: str | None = None
    poll_method: Literal["GET", "POST", "HEAD"] = "GET"
    poll_headers: tuple[tuple[str, str], ...] = ()
    poll_json_path: str = "$"
    poll_fire_on: Literal["change", "match"] = "change"
    poll_match_value: str | None = None
    source_agent_id: UUID | None = None
    cooldown_seconds: int = 0
    max_fires: int | None = None
    expires_at: datetime | None = None
    poll_credential_id: UUID | None = None
    webhook_credential_id: UUID | None = None
    source_membership_id: UUID | None = None
    destination_kind: Literal["session", "group"] | None = None
    destination_id: UUID | None = None
    destination_conversation_id: UUID | None = None

    def __post_init__(self) -> None:
        _validate_destination(self.destination_kind, self.destination_id, self.destination_conversation_id)
        if self.poll_credential_id is not None and (self.kind != "poll" or not isinstance(self.poll_credential_id, UUID)):
            raise InvalidInput("Poll Credential belongs only to poll triggers")
        if self.webhook_credential_id is not None and (self.kind != "webhook" or not isinstance(self.webhook_credential_id, UUID)):
            raise InvalidInput("Webhook Credential belongs only to webhook triggers")
        if self.source_membership_id is not None and (self.kind != "on_message" or not isinstance(self.source_membership_id, UUID)
                or self.source_agent_id is not None):
            raise InvalidInput("Message source must select either a Membership or an Agent")
        if not self.name.strip() or len(self.name) > 200 or not self.instruction.strip():
            raise InvalidInput("Trigger name and instruction are required")
        if len(self.instruction.encode()) > 250 * 1024 or self.kind not in ("cron", "once", "interval", "poll", "on_message", "webhook"):
            raise InvalidInput("Trigger kind or instruction is invalid")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise InvalidInput("Trigger timezone is invalid") from None
        if self.kind == "cron":
            if not self.cron_expression or len(self.cron_expression) > 100 or len(self.cron_expression.split()) != 5 or not croniter.is_valid(self.cron_expression):
                raise InvalidInput("Trigger requires a valid five-field cron expression")
            try:
                croniter(self.cron_expression, datetime(2000, 1, 1, tzinfo=UTC), max_years_between_matches=8).get_next(datetime)
            except CroniterBadDateError:
                raise InvalidInput("Trigger cron expression has no reachable calendar date") from None
        elif self.cron_expression is not None:
            raise InvalidInput("Cron expression belongs only to cron triggers")
        if (self.kind == "once") != (self.at is not None):
            raise InvalidInput("Only one-time triggers require an explicit date")
        if self.at is not None:
            _aware(self.at)
        if self.kind in ("interval", "poll"):
            if type(self.interval_minutes) is not int or not 1 <= self.interval_minutes <= 525600:
                raise InvalidInput("Trigger interval must be between one minute and one year")
        elif self.interval_minutes is not None:
            raise InvalidInput("Interval belongs only to interval or poll triggers")
        if self.kind == "poll":
            parsed = urlsplit(self.poll_url or "")
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or len(self.poll_url or "") > 2048:
                raise InvalidInput("Poll requires an HTTP URL without credentials")
            if any(key.casefold().replace("_", "").replace("-", "") in {"apikey", "token", "accesstoken", "password", "secret"}
                    for key, _ in parse_qsl(parsed.query)):
                raise InvalidInput("Poll URL must not contain credentials")
            if len(self.poll_json_path) > 512 or not self.poll_json_path.startswith("$"):
                raise InvalidInput("Poll JSON path is invalid")
            if self.poll_fire_on not in ("change", "match") or (self.poll_fire_on == "match" and self.poll_match_value is None):
                raise InvalidInput("Poll matching condition is invalid")
            if self.poll_method not in ("GET", "POST", "HEAD") or not isinstance(self.poll_headers, tuple) or len(self.poll_headers) > 32:
                raise InvalidInput("Poll method or headers are invalid")
            names = set()
            for header in self.poll_headers:
                if not isinstance(header, tuple) or len(header) != 2:
                    raise InvalidInput("Poll headers must be immutable name/value pairs")
                key, value = header
                normalized = key.lower().replace("-", "").replace("_", "")
                if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or len(key) > 128
                        or not value.isascii() or any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value)
                        or len(value) > 2048 or "\n" in key + value or "\r" in key + value
                        or normalized in {"authorization", "proxyauthorization", "cookie", "setcookie", "xapikey", "apikey", "token"}
                        or key.lower() in names):
                    raise InvalidInput("Poll headers must be bounded non-Secret values")
                names.add(key.lower())
        elif (self.poll_url is not None or self.poll_match_value is not None or self.poll_json_path != "$"
                or self.poll_fire_on != "change" or self.poll_method != "GET" or self.poll_headers):
            raise InvalidInput("Poll options belong only to poll triggers")
        if self.kind != "on_message" and self.source_agent_id is not None:
            raise InvalidInput("Message source belongs only to message triggers")
        if type(self.cooldown_seconds) is not int or not 0 <= self.cooldown_seconds <= 31536000:
            raise InvalidInput("Trigger cooldown is invalid")
        if self.max_fires is not None and (type(self.max_fires) is not int or not 1 <= self.max_fires <= 2**31 - 1):
            raise InvalidInput("Trigger fire limit is invalid")
        if self.expires_at is not None:
            _aware(self.expires_at)
        if len(TypeAdapter(TriggerConfig).dump_json(self)) > 256 * 1024:
            raise InvalidInput("Trigger configuration is too large")


@dataclass(frozen=True, slots=True)
class TriggerView:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    config: TriggerConfig
    enabled: bool
    delegated_connection_ids: tuple[UUID, ...]
    fire_count: int
    last_fired_at: datetime | None
    removed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TriggerDue:
    trigger: TriggerView
    due_at: datetime
    source_key: str


@dataclass(frozen=True, slots=True)
class TriggerDuePage:
    items: tuple[TriggerDue, ...]
    next_after_id: UUID | None
    errors: tuple["TriggerDueError", ...] = ()


@dataclass(frozen=True, slots=True)
class TriggerDueError:
    tenant_id: UUID
    agent_id: UUID
    trigger_id: UUID
    code: str


@dataclass(frozen=True, slots=True)
class TriggerExecutionResult:
    run_id: UUID
    status: Literal["Completed", "Failed", "Cancelled", "Interrupted"]
    reason: str | None
    output_preview: str
    output_truncated: bool


@dataclass(frozen=True, slots=True)
class TriggerHistoryPage:
    items: tuple["TriggerOccurrence", ...]
    next_after_id: UUID | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class TriggerOccurrence:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    trigger_id: UUID
    source_key: str
    due_at: datetime
    input: InputContent
    delegated_connection_ids: tuple[UUID, ...]
    admission: Literal["pending", "started", "failed"]
    run_id: UUID | None
    result: TriggerExecutionResult | None
    destination_kind: Literal["session", "group"] | None = None
    destination_id: UUID | None = None
    destination_conversation_id: UUID | None = None
    origin_kind: Literal["agent", "membership", "group"] | None = None
    origin_id: UUID | None = None
    origin_conversation_id: UUID | None = None

    @property
    def source(self) -> SourceIdentity:
        return SourceIdentity("trigger", self.id, self.source_key)


async def _checked_destination(tx: TransactionContext, *, agent_id: UUID, config: TriggerConfig,
        principal: TenantPrincipal | None = None, origin_run: RunView | None = None) -> TriggerConfig:
    if config.destination_kind is None:
        return config
    assert config.destination_id is not None
    if principal is not None:
        if config.destination_kind == "session":
            session = await SessionService(tx).get(principal, session_id=config.destination_id)
            if session.agent_id != agent_id:
                raise AccessDenied("Scheduled Session destination belongs to another Agent")
            return config
        conversation = await GroupService(tx).authorize_destination(principal, group_id=config.destination_id,
            agent_id=agent_id, conversation_id=config.destination_conversation_id)
        return replace(config, destination_conversation_id=conversation)
    if origin_run is None or origin_run.agent_id != agent_id or origin_run.parent_run_id is not None:
        raise AccessDenied("Native scheduled destinations require the current authorized Main origin")
    origin_run = await RunService(tx).get(tenant_id=origin_run.tenant_id, run_id=origin_run.id)
    if origin_run.agent_id != agent_id or origin_run.parent_run_id is not None or origin_run.status != "Running":
        raise AccessDenied("Native scheduled destinations require a Running Main")
    if config.destination_kind == "session" and origin_run.source.kind == "session":
        current = await SessionService(tx).get_execution_context(origin_run)
        if current.session.id == config.destination_id:
            return config
    elif config.destination_kind == "group" and origin_run.source.kind == "group":
        group, conversation = await GroupService(tx).execution_destination(origin_run)
        if group == config.destination_id and config.destination_conversation_id in (None, conversation):
            return replace(config, destination_conversation_id=conversation)
    raise AccessDenied("Native schedule may only target its original Session or Group conversation")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidInput("Trigger time requires a timezone")
    return value.astimezone(UTC)


def _bound(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("Trigger page size must be between one and 100")


def _configuration(row: AgentTriggerRecord) -> TriggerConfig:
    if row.configuration_version not in (1, 2, 3) or row.delegation_version != 1:
        raise InvalidInput("Unsupported Trigger configuration version")
    try:
        if set(row.configuration) != {"spec", "fire_count", "last_fired_at", "poll_value_hash", "last_poll_at", "removed_at"}:
            raise ValueError
        if type(row.configuration["fire_count"]) is not int or row.configuration["fire_count"] < 0:
            raise ValueError
        for field in ("last_fired_at", "last_poll_at", "removed_at"):
            if row.configuration[field] is not None:
                _aware(datetime.fromisoformat(row.configuration[field]))
        digest = row.configuration["poll_value_hash"]
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError
        expected = {field.name for field in fields(TriggerConfig)}
        if row.configuration_version < 3:
            expected -= _DESTINATION_FIELDS
        if row.configuration_version == 1:
            expected -= {"poll_credential_id", "webhook_credential_id", "source_membership_id"}
        if set(row.configuration["spec"]) != expected:
            raise ValueError
        return TypeAdapter(TriggerConfig).validate_json(json.dumps(row.configuration["spec"]), strict=True)
    except (ValueError, TypeError, ValidationError):
        raise InvalidInput("Stored Trigger configuration is invalid") from None


def _encode_config(config: TriggerConfig) -> tuple[int, dict[str, object]]:
    spec = TypeAdapter(TriggerConfig).dump_python(config, mode="json")
    if config.destination_kind is not None:
        return 3, spec
    for name in _DESTINATION_FIELDS:
        del spec[name]
    if config.poll_credential_id is None and config.webhook_credential_id is None and config.source_membership_id is None:
        del spec["poll_credential_id"], spec["webhook_credential_id"], spec["source_membership_id"]
        return 1, spec
    return 2, spec


def _delegated(value: list[dict[str, object]]) -> tuple[UUID, ...]:
    try:
        if len(value) > 128 or any(set(item) != {"connection_id"} for item in value):
            raise ValueError
        ids = tuple(UUID(str(item["connection_id"])) for item in value)
        if len(set(ids)) != len(ids):
            raise ValueError
        return ids
    except (ValueError, TypeError, KeyError):
        raise InvalidInput("Stored Trigger delegation is invalid") from None


def _view(row: AgentTriggerRecord) -> TriggerView:
    config = _configuration(row)
    return TriggerView(row.id, row.tenant_id, row.agent_id, config, row.enabled, _delegated(row.delegated_connections),
        row.configuration["fire_count"], datetime.fromisoformat(row.configuration["last_fired_at"]) if row.configuration["last_fired_at"] else None,
        datetime.fromisoformat(row.configuration["removed_at"]) if row.configuration["removed_at"] else None)


def _occurrence(row: TriggerOccurrenceRecord) -> TriggerOccurrence:
    if row.payload_version not in (1, 2, 3, 4) or row.result_version != 1 or row.admission not in ("pending", "started", "failed"):
        raise InvalidInput("Unsupported Trigger occurrence version or admission")
    try:
        expected = {"text", "delegated_connections"}
        if row.payload_version >= 2:
            expected.add("references")
        if row.payload_version >= 3:
            expected |= _DESTINATION_FIELDS
        if row.payload_version == 4:
            expected |= _ORIGIN_FIELDS
        if set(row.payload) != expected:
            raise ValueError
        text = row.payload["text"]
        if not isinstance(text, str) or len(text.encode()) > 256 * 1024:
            raise ValueError
        delegated = _delegated(row.payload["delegated_connections"])
        references = _references(row.payload["references"]) if row.payload_version >= 2 else ()
        destination = _read_destination(row.payload)
        origin = _read_origin(row.payload) if row.payload_version == 4 else (None, None, None)
        if row.result is not None and set(row.result) != {field.name for field in fields(TriggerExecutionResult)}:
            raise ValueError
        result = TypeAdapter(TriggerExecutionResult).validate_json(json.dumps(row.result), strict=True) if row.result is not None else None
        if result is not None and (result.run_id != row.run_id or len(result.output_preview) > 512
                or (result.reason is not None and len(result.reason) > 512)):
            raise ValueError
    except (ValueError, TypeError, ValidationError, KeyError):
        raise InvalidInput("Stored Trigger occurrence is invalid") from None
    return TriggerOccurrence(row.id, row.tenant_id, row.agent_id, row.trigger_id, row.source_key,
        row.due_at, InputContent(text, references), delegated, row.admission, row.run_id, result, *destination, *origin)


def _references(value: object) -> tuple[InputReference, ...]:
    refs = TypeAdapter(tuple[InputReference, ...]).validate_json(json.dumps(value), strict=True)
    if len(refs) > 64 or any(not ref.reference or len(ref.reference) > 4096
            or (ref.name is not None and len(ref.name) > 512)
            or (ref.media_type is not None and len(ref.media_type) > 256) for ref in refs):
        raise InvalidInput("Trigger event references are invalid")
    return refs


def _scheduled(row: AgentTriggerRecord, now: datetime, not_before: datetime) -> datetime | None:
    config = _configuration(row)
    if not row.enabled or row.configuration["removed_at"] is not None or (config.expires_at is not None and now >= _aware(config.expires_at)):
        return None
    if config.max_fires is not None and row.configuration["fire_count"] >= config.max_fires:
        return None
    if config.kind == "cron":
        assert config.cron_expression is not None
        try:
            at = croniter(config.cron_expression, now.astimezone(ZoneInfo(config.timezone)) + timedelta(microseconds=1),
                max_years_between_matches=8).get_prev(datetime)
        except CroniterBadDateError:
            raise InvalidInput("Stored Trigger cron has no reachable calendar date") from None
    elif config.kind == "once":
        assert config.at is not None
        at = config.at
    elif config.kind in ("interval", "poll"):
        assert config.interval_minutes is not None
        interval = timedelta(minutes=config.interval_minutes)
        periods = (now - row.created_at) // interval
        if periods < 1:
            return None
        at = row.created_at + periods * interval
    else:
        return None
    at = _aware(at)
    if at > now or at < not_before or at <= row.created_at:
        return None
    last = row.configuration["last_poll_at" if config.kind == "poll" else "last_fired_at"]
    if last is not None and at <= _aware(datetime.fromisoformat(last)):
        return None
    return at


class TriggerService:
    """Short caller-owned transactions. External polling and webhook authentication precede intake."""

    def __init__(self, transaction: TransactionContext, *, enabled_sources: EnabledSources | None = None) -> None:
        self._tx, self._session, self._enabled_sources = transaction, transaction.session, enabled_sources

    async def create(self, principal: TenantPrincipal, *, agent_id: UUID, config: TriggerConfig,
            enabled: bool = True, delegated_connection_ids: tuple[UUID, ...] = (), now: datetime | None = None) -> TriggerView:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        config.__post_init__()
        config = await _checked_destination(self._tx, agent_id=agent_id, config=config, principal=principal)
        delegated = await self._validate_delegation(principal, agent_id, delegated_connection_ids)
        return await self._create_record(principal.tenant_id, agent_id, config, enabled, delegated, now)

    async def create_for_agent(self, scope: AgentToolResolutionScope, *, config: TriggerConfig,
            enabled: bool = True, now: datetime | None = None, origin_run: RunView | None = None) -> TriggerView:
        delegated = await self._validate_agent(scope)
        config.__post_init__()
        if origin_run is not None and origin_run.tenant_id != scope.tenant_id:
            raise AccessDenied("Scheduled origin belongs to another Tenant")
        config = await _checked_destination(self._tx, agent_id=scope.agent_id, config=config, origin_run=origin_run)
        return await self._create_record(scope.tenant_id, scope.agent_id, config, enabled, delegated, now)

    async def _create_record(self, tenant_id: UUID, agent_id: UUID, config: TriggerConfig, enabled: bool,
            delegated: builtins.list[dict[str, str]], now: datetime | None) -> TriggerView:
        stamp = _aware(now or datetime.now(UTC))
        if enabled and config.kind == "once" and config.at is not None and _aware(config.at) <= stamp:
            raise InvalidInput("One-time Trigger must be scheduled in the future")
        if type(enabled) is not bool:
            raise InvalidInput("Trigger enabled must be boolean")
        await self._validate_credentials(tenant_id, agent_id, config)
        version, encoded = _encode_config(config)
        row = AgentTriggerRecord(id=uuid4(), tenant_id=tenant_id, agent_id=agent_id,
            configuration_version=version, configuration={"spec": encoded,
                "fire_count": 0, "last_fired_at": None, "poll_value_hash": None, "last_poll_at": None, "removed_at": None},
            delegation_version=1, delegated_connections=delegated, enabled=enabled, created_at=stamp, updated_at=stamp)
        self._session.add(row)
        await self._session.flush()
        return _view(row)

    async def update(self, principal: TenantPrincipal, *, trigger_id: UUID, config: TriggerConfig,
            enabled: bool, delegated_connection_ids: tuple[UUID, ...] = ()) -> TriggerView:
        row = await self._require(principal.tenant_id, trigger_id)
        await AgentService(self._tx).get_for_execution(principal, agent_id=row.agent_id)
        config.__post_init__()
        config = await _checked_destination(self._tx, agent_id=row.agent_id, config=config, principal=principal)
        delegated = await self._validate_delegation(principal, row.agent_id, delegated_connection_ids)
        return await self._update_record(principal.tenant_id, trigger_id, config, enabled, delegated)

    async def update_for_agent(self, scope: AgentToolResolutionScope, *, trigger_id: UUID,
            config: TriggerConfig, enabled: bool, origin_run: RunView | None = None) -> TriggerView:
        delegated = await self._validate_agent(scope)
        row = await self._require(scope.tenant_id, trigger_id)
        if row.agent_id != scope.agent_id:
            raise AccessDenied("Trigger belongs to another Agent")
        config.__post_init__()
        if origin_run is not None and origin_run.tenant_id != scope.tenant_id:
            raise AccessDenied("Scheduled origin belongs to another Tenant")
        config = await _checked_destination(self._tx, agent_id=scope.agent_id, config=config, origin_run=origin_run)
        return await self._update_record(scope.tenant_id, trigger_id, config, enabled, delegated)

    async def _update_record(self, tenant_id: UUID, trigger_id: UUID, config: TriggerConfig, enabled: bool,
            delegated: builtins.list[dict[str, str]]) -> TriggerView:
        if type(enabled) is not bool:
            raise InvalidInput("Trigger enabled must be boolean")
        if enabled and config.kind == "once" and config.at is not None and _aware(config.at) <= datetime.now(UTC):
            raise InvalidInput("One-time Trigger must be scheduled in the future")
        row = await self._require(tenant_id, trigger_id, lock=True)
        if row.configuration["removed_at"] is not None:
            raise Conflict("Removed Trigger configuration cannot be changed")
        await self._validate_credentials(tenant_id, row.agent_id, config)
        version, spec = _encode_config(config)
        changed = spec != row.configuration["spec"]
        row.configuration = {**row.configuration, "spec": spec,
            "poll_value_hash": None if changed else row.configuration["poll_value_hash"],
            "last_poll_at": None if changed else row.configuration["last_poll_at"]}
        row.enabled, row.delegated_connections, row.updated_at = enabled, delegated, datetime.now(UTC)
        row.configuration_version = version
        await self._session.flush()
        return _view(row)

    async def remove(self, principal: TenantPrincipal, *, trigger_id: UUID) -> TriggerView:
        row = await self._require(principal.tenant_id, trigger_id)
        await AgentService(self._tx).get_for_execution(principal, agent_id=row.agent_id)
        return await self._remove_record(principal.tenant_id, trigger_id)

    async def remove_for_agent(self, scope: AgentToolResolutionScope, *, trigger_id: UUID) -> TriggerView:
        await self._validate_agent(scope)
        row = await self._require(scope.tenant_id, trigger_id)
        if row.agent_id != scope.agent_id:
            raise AccessDenied("Trigger belongs to another Agent")
        return await self._remove_record(scope.tenant_id, trigger_id)

    async def _remove_record(self, tenant_id: UUID, trigger_id: UUID) -> TriggerView:
        row = await self._require(tenant_id, trigger_id, lock=True)
        if row.configuration["removed_at"] is None:
            row.enabled = False
            row.updated_at = datetime.now(UTC)
            row.configuration = {**row.configuration, "removed_at": row.updated_at.isoformat()}
            await self._session.flush()
        return _view(row)

    async def get(self, principal: TenantPrincipal, *, trigger_id: UUID) -> TriggerView:
        row = await self._require(principal.tenant_id, trigger_id)
        await AgentService(self._tx).get_for_execution(principal, agent_id=row.agent_id)
        return _view(row)

    async def get_for_intake(self, *, tenant_id: UUID, trigger_id: UUID) -> TriggerView:
        """Trusted authenticated event adapters inspect one current configuration without fabricating a human."""
        row = await self._require(tenant_id, trigger_id)
        if not row.enabled or row.configuration["removed_at"] is not None:
            raise NotFound("Trigger is unavailable")
        return _view(row)

    async def _validate_credentials(self, tenant_id: UUID, agent_id: UUID, config: TriggerConfig) -> None:
        if config.source_membership_id is not None:
            await IdentityService(self._tx).require_membership(tenant_id=tenant_id, membership_id=config.source_membership_id)
        if config.source_agent_id is not None:
            await AgentService(self._tx).get_metadata(tenant_id=tenant_id, agent_id=config.source_agent_id)
        credentials = CredentialService(self._tx)
        for credential_id in (config.poll_credential_id, config.webhook_credential_id):
            if credential_id is None:
                continue
            try:
                await credentials.require_owner_metadata(tenant_id=tenant_id, credential_id=credential_id,
                    owner_kind="agent", owner_id=agent_id)
            except NotFound:
                await credentials.require_owner_metadata(tenant_id=tenant_id, credential_id=credential_id,
                    owner_kind="tenant", owner_id=tenant_id)

    async def get_for_agent(self, scope: AgentToolResolutionScope, *, trigger_id: UUID) -> TriggerView:
        await self._validate_agent(scope)
        row = await self._require(scope.tenant_id, trigger_id)
        if row.agent_id != scope.agent_id:
            raise AccessDenied("Trigger belongs to another Agent")
        return _view(row)

    async def list(self, principal: TenantPrincipal, *, agent_id: UUID, limit: int = 100,
            after_id: UUID | None = None) -> tuple[TriggerView, ...]:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        return await self._list(principal.tenant_id, agent_id, limit, after_id)

    async def list_for_agent(self, scope: AgentToolResolutionScope, *, limit: int = 100,
            after_id: UUID | None = None) -> tuple[TriggerView, ...]:
        await self._validate_agent(scope)
        return await self._list(scope.tenant_id, scope.agent_id, limit, after_id)

    async def _list(self, tenant_id: UUID, agent_id: UUID, limit: int, after_id: UUID | None) -> tuple[TriggerView, ...]:
        _bound(limit)
        query = select(AgentTriggerRecord).where(AgentTriggerRecord.tenant_id == tenant_id, AgentTriggerRecord.agent_id == agent_id,
            AgentTriggerRecord.configuration["removed_at"].as_string().is_(None))
        if after_id is not None:
            query = query.where(AgentTriggerRecord.id > after_id)
        return tuple(_view(row) for row in (await self._session.scalars(query.order_by(AgentTriggerRecord.id).limit(limit))).all())

    async def due(self, *, now: datetime, not_before: datetime, limit: int = 100,
            after_id: UUID | None = None) -> TriggerDuePage:
        _bound(limit)
        now, not_before = _aware(now), _aware(not_before)
        if not_before > now:
            raise InvalidInput("Trigger scan window is invalid")
        query = select(AgentTriggerRecord).where(AgentTriggerRecord.enabled.is_(True))
        if after_id is not None:
            query = query.where(AgentTriggerRecord.id > after_id)
        rows = (await self._session.scalars(query.order_by(AgentTriggerRecord.id).limit(limit))).all()
        candidates, errors = [], []
        for row in rows:
            try:
                at = _scheduled(row, now, not_before)
                if at is not None:
                    candidates.append(TriggerDue(_view(row), at, at.isoformat()))
            except InvalidInput:
                errors.append(TriggerDueError(row.tenant_id, row.agent_id, row.id, "invalid_configuration"))
        return TriggerDuePage(tuple(candidates), rows[-1].id if len(rows) == limit else None, tuple(errors))

    async def accept(self, *, tenant_id: UUID, trigger_id: UUID, source_key: str, now: datetime,
            input: InputContent | None = None, due_at: datetime | None = None, not_before: datetime | None = None,
            event_kind: Literal["on_message", "webhook", "manual"] | None = None,
            source_agent_id: UUID | None = None, source_membership_id: UUID | None = None,
            expected_config: TriggerConfig | None = None, origin: WorkspaceSubject | None = None,
            origin_conversation_id: UUID | None = None) -> TriggerOccurrence:
        now = _aware(now)
        row = await self._require(tenant_id, trigger_id, lock=True)
        existing = await self._find_occurrence(tenant_id, trigger_id, source_key)
        if existing is not None:
            return _occurrence(existing)
        config = _configuration(row)
        if expected_config is not None and config != expected_config:
            raise Conflict("Trigger configuration changed during event authentication")
        if config.kind == "poll" and event_kind != "manual":
            raise InvalidInput("Poll results require observation intake")
        if event_kind is None:
            if due_at is None or not_before is None or _scheduled(row, now, _aware(not_before)) != _aware(due_at) or source_key != _aware(due_at).isoformat():
                raise Conflict("Trigger occurrence is not currently due")
        elif event_kind != "manual":
            if event_kind != config.kind:
                raise InvalidInput("Trigger event kind differs from configuration")
            if event_kind == "on_message":
                if origin is None:
                    raise InvalidInput("Message Trigger requires its authorized origin scope")
                if (origin.kind == "membership" and source_membership_id is not None and origin.id != source_membership_id
                        or origin.kind == "agent" and (origin.id not in (row.agent_id, source_agent_id) or source_membership_id is not None)):
                    raise AccessDenied("Trigger origin differs from the authorized sender scope")
                if ((source_agent_id is None) == (source_membership_id is None) or config.source_agent_id not in (None, source_agent_id)
                        or config.source_membership_id not in (None, source_membership_id)):
                    raise InvalidInput("Trigger message source differs from configuration")
                if source_agent_id is not None:
                    await AgentService(self._tx).get_metadata(tenant_id=tenant_id, agent_id=source_agent_id)
                if source_membership_id is not None:
                    await IdentityService(self._tx).require_membership(tenant_id=tenant_id, membership_id=source_membership_id)
        return await self._insert_occurrence(row, config, source_key, now, _aware(due_at) if due_at is not None else now, input,
            origin=origin, origin_conversation_id=origin_conversation_id, source_membership_id=source_membership_id)

    async def observe_poll(self, *, tenant_id: UUID, trigger_id: UUID, due_at: datetime, now: datetime,
            not_before: datetime, value: str, expected_config: TriggerConfig) -> TriggerOccurrence | None:
        """Caller supplies the bounded extracted HTTP value; no network access occurs while locked."""
        now, due_at = _aware(now), _aware(due_at)
        if not isinstance(value, str) or len(value.encode()) > 128 * 1024:
            raise InvalidInput("Poll observation is too large")
        row = await self._require(tenant_id, trigger_id, lock=True)
        existing = await self._find_occurrence(tenant_id, trigger_id, due_at.isoformat())
        if existing is not None:
            return _occurrence(existing)
        config = _configuration(row)
        if config.kind != "poll":
            raise InvalidInput("Trigger is not an HTTP poll")
        if config != expected_config:
            raise Conflict("Poll configuration changed during observation")
        if row.configuration["last_poll_at"] == due_at.isoformat():
            return None
        if _scheduled(row, now, _aware(not_before)) != due_at:
            raise Conflict("Poll observation is not currently due")
        digest = hashlib.sha256(value.encode()).hexdigest()
        previous = row.configuration["poll_value_hash"]
        fire = value == config.poll_match_value if config.poll_fire_on == "match" else previous is not None and previous != digest
        row.configuration = {**row.configuration, "poll_value_hash": digest, "last_poll_at": due_at.isoformat()}
        if not fire:
            await self._session.flush()
            return None
        return await self._insert_occurrence(row, config, due_at.isoformat(), now, due_at, InputContent(value))

    async def _insert_occurrence(self, row: AgentTriggerRecord, config: TriggerConfig, source_key: str,
            now: datetime, due_at: datetime, input: InputContent | None, *, origin: WorkspaceSubject | None = None,
            origin_conversation_id: UUID | None = None, source_membership_id: UUID | None = None) -> TriggerOccurrence:
        SourceIdentity("trigger", row.id, source_key)
        if due_at > now:
            raise InvalidInput("Trigger occurrence cannot be accepted before it is due")
        if not row.enabled or row.configuration["removed_at"] is not None or (config.expires_at is not None and now >= _aware(config.expires_at)):
            raise Conflict("Trigger is disabled or expired")
        if config.max_fires is not None and row.configuration["fire_count"] >= config.max_fires:
            raise Conflict("Trigger fire limit was reached")
        last = row.configuration["last_fired_at"]
        if last is not None and now < datetime.fromisoformat(last) + timedelta(seconds=config.cooldown_seconds):
            raise Conflict("Trigger cooldown is active")
        agent = await AgentService(self._tx).get_metadata(tenant_id=row.tenant_id, agent_id=row.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Trigger Agent is unavailable")
        if input is not None and input.references and config.kind != "on_message":
            raise InvalidInput("Trigger event references require an explicit attachment adapter")
        text = config.instruction + ("\n\n[Trigger event]\n" + input.text if input is not None else "")
        payload = {"text": text, "delegated_connections": row.delegated_connections}
        if input is not None and input.references:
            payload["references"] = [asdict(ref) for ref in _references([asdict(ref) for ref in input.references])]
        payload.update(_destination_payload(config))
        payload.setdefault("references", [])
        connections = _delegated(row.delegated_connections)
        if origin is None:
            kind, identity, _ = await self._origin(row.tenant_id, row.agent_id, connections)
            origin = WorkspaceSubject(kind, identity)
        elif connections:
            _, owner, _ = await self._origin(row.tenant_id, row.agent_id, connections)
            if (source_membership_id != owner or origin.kind == "agent"
                    or origin.kind == "membership" and origin.id != owner):
                raise AccessDenied("Message origin does not authorize this scheduled personal account")
        payload.update({"origin_kind": origin.kind, "origin_id": str(origin.id),
            "origin_conversation_id": str(origin_conversation_id) if origin_conversation_id else None})
        _read_origin(payload)
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 256 * 1024:
            raise InvalidInput("Trigger occurrence input is too large")
        occurrence = TriggerOccurrenceRecord(id=uuid4(), tenant_id=row.tenant_id, agent_id=row.agent_id, trigger_id=row.id,
            source_key=source_key, due_at=due_at, payload_version=4, payload=payload, run_id=None, admission="pending",
            admission_error=None, result_version=1, result=None, created_at=now, updated_at=now)
        self._session.add(occurrence)
        row.configuration = {**row.configuration, "fire_count": row.configuration["fire_count"] + 1, "last_fired_at": due_at.isoformat()}
        if config.kind == "once" or (config.max_fires is not None and row.configuration["fire_count"] >= config.max_fires):
            row.enabled = False
        await self._session.flush()
        return _occurrence(occurrence)

    async def _origin(self, tenant_id: UUID, agent_id: UUID, connections: tuple[UUID, ...], *,
            run_id: UUID | None = None, message_id: UUID | None = None,
            connection_owners: dict[UUID, UUID] | None = None) -> tuple[Literal["agent", "membership", "group"], UUID, UUID | None]:
        if run_id is not None:
            captured = await RunService(self._tx).read_snapshot(tenant_id=tenant_id, run_id=run_id)
            if captured.agent_id != agent_id:
                raise InvalidInput("Legacy scheduled Snapshot belongs to another Agent")
            output = captured.workspace.output
            if output.kind == "membership":
                return "membership", output.id, None
            if output.kind == "group":
                if message_id is None:
                    raise InvalidInput("Legacy scheduled Group origin requires provenance backfill")
                conversation = await GroupService(self._tx).event_conversation(tenant_id=tenant_id, group_id=output.id, event_id=message_id)
                return "group", output.id, conversation
            owners = {tool.credential.owner_id for tool in captured.tools.tools
                if tool.credential is not None and tool.credential.owner_kind == "membership"}
            if len(owners) == 1:
                return "membership", next(iter(owners)), None
            if len(owners) > 1:
                raise InvalidInput("Scheduled result spans multiple private account owners")
            if message_id is not None:
                raise InvalidInput("Legacy Agent-message visibility requires provenance backfill")
            return "agent", agent_id, None
        if connections:
            owners = ({identity: connection_owners[identity] for identity in connections if identity in connection_owners}
                if connection_owners is not None else await ToolService(self._tx).personal_connection_owners(tenant_id=tenant_id, connection_ids=connections))
            if set(owners) != set(connections) or len(set(owners.values())) != 1:
                raise InvalidInput("Legacy scheduled account origin requires provenance backfill")
            return "membership", next(iter(owners.values())), None
        if message_id is not None:
            raise InvalidInput("Legacy message origin requires provenance backfill before reading its content")
        return "agent", agent_id, None

    async def history(self, principal: TenantPrincipal, *, trigger_id: UUID, limit: int = 100,
            after_id: UUID | None = None, max_bytes: int = 1024 * 1024) -> TriggerHistoryPage:
        await self.get(principal, trigger_id=trigger_id)
        _bound(limit)
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 16 * 1024 * 1024:
            raise InvalidInput("Trigger history byte bound is invalid")
        query = select(TriggerOccurrenceRecord).where(TriggerOccurrenceRecord.tenant_id == principal.tenant_id,
            TriggerOccurrenceRecord.trigger_id == trigger_id)
        if after_id is not None:
            query = query.where(TriggerOccurrenceRecord.id > after_id)
        sizes = (await self._session.execute(query.with_only_columns(TriggerOccurrenceRecord.id,
            func.octet_length(cast(TriggerOccurrenceRecord.payload, Text)), func.octet_length(cast(TriggerOccurrenceRecord.result, Text)),
            TriggerOccurrenceRecord.payload_version, TriggerOccurrenceRecord.run_id, TriggerOccurrenceRecord.source_key, TriggerOccurrenceRecord.agent_id,
            func.left(TriggerOccurrenceRecord.payload["origin_kind"].as_string(), 32),
            func.left(TriggerOccurrenceRecord.payload["origin_id"].as_string(), 64),
            func.left(TriggerOccurrenceRecord.payload["origin_conversation_id"].as_string(), 64))
            .order_by(TriggerOccurrenceRecord.id).limit(limit + 1))).all()
        for metadata in sizes[:limit]:
            if metadata[3] not in (1, 2, 3, 4) or metadata[1] > 256 * 1024 + 4096 or (metadata[2] or 0) > 8192:
                raise InvalidInput("Stored Trigger occurrence exceeds its version or size boundary")
        legacy_ids = [metadata[0] for metadata in sizes[:limit] if metadata[3] < 4]
        legacy_connections: dict[UUID, tuple[UUID, ...]] = {}
        if legacy_ids:
            saved = (await self._session.execute(select(TriggerOccurrenceRecord.id, TriggerOccurrenceRecord.payload["delegated_connections"]).where(
                TriggerOccurrenceRecord.tenant_id == principal.tenant_id, TriggerOccurrenceRecord.id.in_(legacy_ids),
                func.octet_length(cast(TriggerOccurrenceRecord.payload["delegated_connections"], Text)) <= 8192))).all()
            if len(saved) != len(legacy_ids):
                raise InvalidInput("Legacy scheduled delegation metadata is unavailable or oversized")
            for identity, values in saved:
                if not isinstance(values, list):
                    raise InvalidInput("Legacy scheduled delegation metadata is invalid")
                legacy_connections[identity] = _delegated(values)
        connection_ids = tuple({identity for metadata in sizes[:limit] if metadata[4] is None
            for identity in legacy_connections.get(metadata[0], ())})
        connection_owners: dict[UUID, UUID] = {}
        for offset in range(0, len(connection_ids), 128):
            connection_owners.update(await ToolService(self._tx).personal_connection_owners(tenant_id=principal.tenant_id,
                connection_ids=connection_ids[offset:offset + 128]))
        resolved = []
        for id, payload_size, result_size, version, run_id, source_key, source_agent, kind, identity, conversation in sizes[:limit]:
            if version == 4:
                try:
                    origin = _read_origin({"origin_kind": kind, "origin_id": identity, "origin_conversation_id": conversation})
                except (ValidationError, ValueError, TypeError):
                    raise InvalidInput("Stored scheduled origin is invalid") from None
            else:
                message_id = UUID(source_key[len("message:"):]) if source_key.startswith("message:") else None
                origin = await self._origin(principal.tenant_id, source_agent, legacy_connections[id], run_id=run_id,
                    message_id=message_id, connection_owners=connection_owners)
            resolved.append((id, payload_size, result_size, origin))
        groups = tuple({origin[1] for _, _, _, origin in resolved if origin[0] == "group"})
        readable = await GroupService(self._tx).authorized_group_ids(principal, group_ids=groups) if groups else frozenset()
        selected, used, scanned, cursor = [], 1024, 0, None
        for id, payload_size, result_size, origin in resolved:
            if not (origin[0] == "agent" and (principal.can_manage_all_agents or origin[1] in principal.allowed_agent_ids)
                    or origin[0] == "membership" and origin[1] == principal.membership_id
                    or origin[0] == "group" and origin[1] in readable):
                scanned, cursor = scanned + 1, id
                continue
            cost = payload_size + (result_size or 0) + 2048
            if used + cost > max_bytes:
                if not selected:
                    raise InvalidInput("Trigger occurrence cannot fit the requested page")
                break
            selected.append(id)
            used += cost
            scanned, cursor = scanned + 1, id
        rows = (await self._session.scalars(query.where(TriggerOccurrenceRecord.id.in_(selected),
            func.octet_length(cast(TriggerOccurrenceRecord.payload, Text)) <= 256 * 1024 + 4096,
            or_(TriggerOccurrenceRecord.result.is_(None), func.octet_length(cast(TriggerOccurrenceRecord.result, Text)) <= 8192))
            .order_by(TriggerOccurrenceRecord.id))).all() if selected else []
        if len(rows) != len(selected):
            raise InvalidInput("Trigger history changed during its bounded read")
        origins = {id: origin for id, _, _, origin in resolved}
        return TriggerHistoryPage(tuple(replace(_occurrence(row), origin_kind=origins[row.id][0],
            origin_id=origins[row.id][1], origin_conversation_id=origins[row.id][2]) for row in rows),
            cursor, len(sizes) > scanned)

    async def get_occurrence(self, *, tenant_id: UUID, occurrence_id: UUID) -> TriggerOccurrence:
        value = _occurrence(await self._require_occurrence(tenant_id, occurrence_id))
        if value.origin_kind is None:
            message_id = UUID(value.source_key[len("message:"):]) if value.source_key.startswith("message:") else None
            kind, identity, conversation = await self._origin(tenant_id, value.agent_id, value.delegated_connection_ids,
                run_id=value.run_id, message_id=message_id)
            return replace(value, origin_kind=kind, origin_id=identity, origin_conversation_id=conversation)
        return value

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        if transaction is not self._tx:
            return await TriggerService(transaction).record_started(transaction, run=run)
        row = await self._from_run(run)
        if row.run_id is not None and row.run_id != run.id:
            raise Conflict("Trigger occurrence already has a Run")
        row.admission, row.run_id, row.admission_error = "started", run.id, None
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if transaction is not self._tx:
            return await TriggerService(transaction).record_outcome(transaction, run=run, outcome=outcome)
        if run.status != outcome.status:
            raise InvalidInput("Trigger outcome must match the terminal Run")
        row = await self._from_run(run)
        if row.run_id != run.id:
            raise Conflict("Trigger result has no started Run")
        result = {"run_id": str(run.id), "status": outcome.status, "reason": outcome.reason[:512] if outcome.reason else None,
            "output_preview": outcome.output[:512], "output_truncated": len(outcome.output) > 512}
        if row.result is not None and row.result != result:
            raise Conflict("Trigger outcome is immutable")
        row.result = result
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def fail_admission(self, *, tenant_id: UUID, occurrence_id: UUID, reason: str) -> None:
        if not reason or len(reason.encode()) > 512:
            raise InvalidInput("Trigger admission failure is invalid")
        row = await self._require_occurrence(tenant_id, occurrence_id, lock=True)
        if row.run_id is None:
            row.admission, row.admission_error = "failed", reason
            row.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def _from_run(self, run: RunView) -> TriggerOccurrenceRecord:
        if run.source.kind != "trigger" or run.parent_run_id is not None:
            raise InvalidInput("Trigger requires its own Main Run source")
        row = await self._require_occurrence(run.tenant_id, run.source.owner_id, lock=True)
        if row.agent_id != run.agent_id or row.source_key != run.source.key:
            raise Conflict("Trigger Run source differs from its occurrence")
        return row

    async def _require_occurrence(self, tenant_id: UUID, occurrence_id: UUID, *, lock: bool = False) -> TriggerOccurrenceRecord:
        query = select(TriggerOccurrenceRecord).where(TriggerOccurrenceRecord.tenant_id == tenant_id, TriggerOccurrenceRecord.id == occurrence_id)
        size = (await self._session.execute(query.with_only_columns(func.octet_length(cast(TriggerOccurrenceRecord.payload, Text)),
            func.octet_length(cast(TriggerOccurrenceRecord.result, Text))))).one_or_none()
        if size is None:
            raise NotFound("Trigger occurrence is unavailable")
        if size[0] > 256 * 1024 + 4096 or (size[1] or 0) > 8192:
            raise InvalidInput("Stored Trigger occurrence exceeds its bound")
        query = query.where(func.octet_length(cast(TriggerOccurrenceRecord.payload, Text)) <= 256 * 1024 + 4096,
            or_(TriggerOccurrenceRecord.result.is_(None), func.octet_length(cast(TriggerOccurrenceRecord.result, Text)) <= 8192))
        row = await self._session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if row is None:
            raise NotFound("Trigger occurrence is unavailable")
        _occurrence(row)
        return row

    async def _require(self, tenant_id: UUID, trigger_id: UUID, *, lock: bool = False) -> AgentTriggerRecord:
        query = select(AgentTriggerRecord).where(AgentTriggerRecord.tenant_id == tenant_id, AgentTriggerRecord.id == trigger_id)
        row = await self._session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if row is None:
            raise NotFound("Trigger is unavailable")
        _configuration(row)
        return row

    async def _find_occurrence(self, tenant_id: UUID, trigger_id: UUID, source_key: str) -> TriggerOccurrenceRecord | None:
        id = await self._session.scalar(select(TriggerOccurrenceRecord.id).where(TriggerOccurrenceRecord.tenant_id == tenant_id,
            TriggerOccurrenceRecord.trigger_id == trigger_id, TriggerOccurrenceRecord.source_key == source_key))
        return await self._require_occurrence(tenant_id, id) if id is not None else None

    async def _validate_delegation(self, principal: TenantPrincipal, agent_id: UUID,
            ids: tuple[UUID, ...]) -> builtins.list[dict[str, str]]:
        if len(set(ids)) != len(ids):
            raise InvalidInput("Trigger delegation contains duplicates")
        if ids:
            await ToolService(self._tx, enabled_sources=self._enabled_sources).capture_authorized(ToolResolutionScope(
                principal, agent_id, "main", frozenset(ids), ids))
        return [{"connection_id": str(id)} for id in ids]

    async def _validate_agent(self, scope: AgentToolResolutionScope) -> builtins.list[dict[str, str]]:
        if scope.role != "main":
            raise AccessDenied("Only Main can configure Trigger execution")
        agent = await AgentService(self._tx).get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Trigger Agent is unavailable")
        ids = scope.selected_personal_connections
        if len(set(ids)) != len(ids) or not set(ids) <= scope.authorized_personal_connections:
            raise AccessDenied("Trigger account delegation was not authorized")
        if ids:
            await ToolService(self._tx, enabled_sources=self._enabled_sources).capture_authorized(scope)
        return [{"connection_id": str(id)} for id in ids]
