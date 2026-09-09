"""Heartbeat configuration and occurrences; never creates Trigger records."""

import json
import re
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text, cast, func, or_, select, tuple_

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.group.public import GroupService
from app.modules.heartbeat.models import AgentHeartbeatRecord, HeartbeatOccurrenceRecord
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import InputContent, RunService, RunView, SourceIdentity, TerminalOutcomePayload
from app.modules.session.public import SessionService
from app.modules.tool.public import AgentToolResolutionScope, EnabledSources, ToolResolutionScope, ToolService


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


def _destination_payload(config: "HeartbeatConfig") -> dict[str, object]:
    return {"destination_kind": config.destination_kind,
        "destination_id": str(config.destination_id) if config.destination_id is not None else None,
        "destination_conversation_id": str(config.destination_conversation_id) if config.destination_conversation_id is not None else None}


@dataclass(frozen=True, slots=True)
class HeartbeatConfig:
    instruction: str
    interval_minutes: int
    timezone: str = "UTC"
    active_start: str = "00:00"
    active_end: str = "00:00"
    destination_kind: Literal["session", "group"] | None = None
    destination_id: UUID | None = None
    destination_conversation_id: UUID | None = None

    def __post_init__(self) -> None:
        _validate_destination(self.destination_kind, self.destination_id, self.destination_conversation_id)
        if not self.instruction.strip() or len(self.instruction.encode()) > 250 * 1024:
            raise InvalidInput("Heartbeat instruction is empty or too large")
        if type(self.interval_minutes) is not int or not 1 <= self.interval_minutes <= 525600:
            raise InvalidInput("Heartbeat interval must be between one minute and one year")
        try:
            ZoneInfo(self.timezone)
            for value in (self.active_start, self.active_end):
                if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
                    raise ValueError
        except (ValueError, ZoneInfoNotFoundError):
            raise InvalidInput("Heartbeat timezone or active hours are invalid") from None


@dataclass(frozen=True, slots=True)
class HeartbeatView:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    config: HeartbeatConfig
    enabled: bool
    delegated_connection_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class HeartbeatDue:
    heartbeat: HeartbeatView
    due_at: datetime
    source_key: str


@dataclass(frozen=True, slots=True)
class HeartbeatDuePage:
    items: tuple[HeartbeatDue, ...]
    next_after_id: UUID | None
    errors: tuple["HeartbeatDueError", ...] = ()


@dataclass(frozen=True, slots=True)
class HeartbeatDueError:
    tenant_id: UUID
    agent_id: UUID
    heartbeat_id: UUID
    code: str


@dataclass(frozen=True, slots=True)
class HeartbeatExecutionResult:
    run_id: UUID
    status: Literal["Completed", "Failed", "Cancelled", "Interrupted"]
    reason: str | None
    output_preview: str
    output_truncated: bool


@dataclass(frozen=True, slots=True)
class HeartbeatHistoryPage:
    items: tuple["HeartbeatOccurrence", ...]
    next_after_id: UUID | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class HeartbeatOccurrence:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    heartbeat_id: UUID
    source_key: str
    due_at: datetime
    input: InputContent
    delegated_connection_ids: tuple[UUID, ...]
    admission: Literal["pending", "started", "failed"]
    run_id: UUID | None
    result: HeartbeatExecutionResult | None
    destination_kind: Literal["session", "group"] | None = None
    destination_id: UUID | None = None
    destination_conversation_id: UUID | None = None
    origin_kind: Literal["agent", "membership", "group"] | None = None
    origin_id: UUID | None = None
    origin_conversation_id: UUID | None = None

    @property
    def source(self) -> SourceIdentity:
        return SourceIdentity("heartbeat", self.id, self.source_key)


async def _checked_destination(tx: TransactionContext, *, agent_id: UUID, config: HeartbeatConfig,
        principal: TenantPrincipal | None = None, origin_run: RunView | None = None) -> HeartbeatConfig:
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
        raise InvalidInput("Heartbeat time requires a timezone")
    return value.astimezone(UTC)


def _bound(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("Heartbeat page size must be between one and 100")


def _config(row: AgentHeartbeatRecord) -> HeartbeatConfig:
    if row.configuration_version not in (1, 2) or row.delegation_version != 1:
        raise InvalidInput("Unsupported Heartbeat configuration version")
    try:
        expected = {field.name for field in fields(HeartbeatConfig)}
        if row.configuration_version == 1:
            expected -= _DESTINATION_FIELDS
        if set(row.configuration) != expected:
            raise ValueError
        return TypeAdapter(HeartbeatConfig).validate_json(json.dumps(row.configuration), strict=True)
    except (ValidationError, TypeError, ValueError):
        raise InvalidInput("Stored Heartbeat configuration is invalid") from None


def _encode_config(config: HeartbeatConfig) -> tuple[int, dict[str, object]]:
    data = TypeAdapter(HeartbeatConfig).dump_python(config, mode="json")
    if config.destination_kind is not None:
        return 2, data
    for name in _DESTINATION_FIELDS:
        del data[name]
    return 1, data


def _delegated(value: list[dict[str, object]]) -> tuple[UUID, ...]:
    try:
        if len(value) > 128 or any(set(item) != {"connection_id"} for item in value):
            raise ValueError
        ids = tuple(UUID(str(item["connection_id"])) for item in value)
        if len(set(ids)) != len(ids):
            raise ValueError
        return ids
    except (ValueError, TypeError, KeyError):
        raise InvalidInput("Stored Heartbeat delegation is invalid") from None


def _view(row: AgentHeartbeatRecord) -> HeartbeatView:
    return HeartbeatView(row.id, row.tenant_id, row.agent_id, _config(row), row.enabled, _delegated(row.delegated_connections))


def _occurrence(row: HeartbeatOccurrenceRecord) -> HeartbeatOccurrence:
    if row.payload_version not in (1, 2, 3) or row.result_version != 1 or row.admission not in ("pending", "started", "failed"):
        raise InvalidInput("Unsupported Heartbeat occurrence version or admission")
    try:
        if set(row.payload) != ({"instruction", "delegated_connections"} | (_DESTINATION_FIELDS if row.payload_version >= 2 else set())
                | (_ORIGIN_FIELDS if row.payload_version == 3 else set())):
            raise ValueError
        instruction = row.payload["instruction"]
        if not isinstance(instruction, str) or len(instruction.encode()) > 256 * 1024:
            raise ValueError
        delegated = _delegated(row.payload["delegated_connections"])
        destination = _read_destination(row.payload)
        origin = _read_origin(row.payload) if row.payload_version == 3 else (None, None, None)
        if row.result is not None and set(row.result) != {field.name for field in fields(HeartbeatExecutionResult)}:
            raise ValueError
        result = TypeAdapter(HeartbeatExecutionResult).validate_json(json.dumps(row.result), strict=True) if row.result is not None else None
        if result is not None and (result.run_id != row.run_id or len(result.output_preview) > 512
                or (result.reason is not None and len(result.reason) > 512)):
            raise ValueError
    except (ValidationError, ValueError, TypeError, KeyError):
        raise InvalidInput("Stored Heartbeat occurrence is invalid") from None
    return HeartbeatOccurrence(row.id, row.tenant_id, row.agent_id, row.heartbeat_id, row.source_key,
        row.due_at, InputContent(instruction), delegated, row.admission, row.run_id, result, *destination, *origin)


def _scheduled(row: AgentHeartbeatRecord, now: datetime, not_before: datetime) -> datetime | None:
    config = _config(row)
    interval = timedelta(minutes=config.interval_minutes)
    periods = (now - row.created_at) // interval
    if periods < 1:
        return None
    candidate = row.created_at + periods * interval
    if candidate < not_before:
        return None
    local = candidate.astimezone(ZoneInfo(config.timezone)).strftime("%H:%M")
    start, end = config.active_start, config.active_end
    active = start == end or (start <= local < end if start < end else local >= start or local < end)
    return candidate if active else None


class HeartbeatService:
    """Caller commits. Due scans use a process-start lower bound, never replay old occurrences."""

    def __init__(self, transaction: TransactionContext, *, enabled_sources: EnabledSources | None = None) -> None:
        self._tx, self._session, self._enabled_sources = transaction, transaction.session, enabled_sources

    async def configure(self, principal: TenantPrincipal, *, agent_id: UUID, config: HeartbeatConfig,
            enabled: bool = True, delegated_connection_ids: tuple[UUID, ...] = (), now: datetime | None = None) -> HeartbeatView:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        config.__post_init__()
        config = await _checked_destination(self._tx, agent_id=agent_id, config=config, principal=principal)
        if type(enabled) is not bool:
            raise InvalidInput("Heartbeat enabled must be boolean")
        if len(set(delegated_connection_ids)) != len(delegated_connection_ids):
            raise InvalidInput("Heartbeat delegation contains duplicates")
        if delegated_connection_ids:
            await ToolService(self._tx, enabled_sources=self._enabled_sources).capture_authorized(ToolResolutionScope(
                principal, agent_id, "main", frozenset(delegated_connection_ids), delegated_connection_ids))
        return await self._configure_record(principal.tenant_id, agent_id, config, enabled, delegated_connection_ids, now)

    async def configure_for_agent(self, scope: AgentToolResolutionScope, *, config: HeartbeatConfig,
            enabled: bool = True, now: datetime | None = None, origin_run: RunView | None = None) -> HeartbeatView:
        if scope.role != "main":
            raise AccessDenied("Only Main can configure Heartbeat execution")
        agent = await AgentService(self._tx).get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Heartbeat Agent is unavailable")
        ids = scope.selected_personal_connections
        if len(set(ids)) != len(ids) or not set(ids) <= scope.authorized_personal_connections:
            raise AccessDenied("Heartbeat account delegation was not authorized")
        if ids:
            await ToolService(self._tx, enabled_sources=self._enabled_sources).capture_authorized(scope)
        config.__post_init__()
        if origin_run is not None and origin_run.tenant_id != scope.tenant_id:
            raise AccessDenied("Scheduled origin belongs to another Tenant")
        config = await _checked_destination(self._tx, agent_id=scope.agent_id, config=config, origin_run=origin_run)
        if type(enabled) is not bool:
            raise InvalidInput("Heartbeat enabled must be boolean")
        return await self._configure_record(scope.tenant_id, scope.agent_id, config, enabled, ids, now)

    async def _configure_record(self, tenant_id: UUID, agent_id: UUID, config: HeartbeatConfig, enabled: bool,
            ids: tuple[UUID, ...], now: datetime | None) -> HeartbeatView:
        stamp = _aware(now or datetime.now(UTC))
        version, encoded = _encode_config(config)
        # The unique Agent relation is serialized on the owning configuration, not on Run.
        from sqlalchemy.dialects.postgresql import insert
        await self._session.execute(insert(AgentHeartbeatRecord).values(id=uuid4(), tenant_id=tenant_id,
            agent_id=agent_id, created_at=stamp, updated_at=stamp, configuration_version=version,
            configuration=encoded, delegation_version=1, delegated_connections=[], enabled=enabled)
            .on_conflict_do_nothing(index_elements=["tenant_id", "agent_id"]))
        row = await self._session.scalar(select(AgentHeartbeatRecord).where(
            AgentHeartbeatRecord.tenant_id == tenant_id, AgentHeartbeatRecord.agent_id == agent_id).with_for_update().execution_options(populate_existing=True))
        assert row is not None
        row.configuration_version, row.configuration, row.enabled, row.updated_at = version, encoded, enabled, stamp
        row.delegated_connections = [{"connection_id": str(id)} for id in ids]
        await self._session.flush()
        return _view(row)

    async def get(self, principal: TenantPrincipal, *, agent_id: UUID) -> HeartbeatView:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        row = await self._session.scalar(select(AgentHeartbeatRecord).where(
            AgentHeartbeatRecord.tenant_id == principal.tenant_id, AgentHeartbeatRecord.agent_id == agent_id))
        if row is None:
            raise NotFound("Heartbeat configuration is unavailable")
        return _view(row)

    async def get_for_agent(self, scope: AgentToolResolutionScope) -> HeartbeatView:
        if scope.role != "main":
            raise AccessDenied("Only Main can inspect Heartbeat configuration")
        agent = await AgentService(self._tx).get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Heartbeat Agent is unavailable")
        row = await self._session.scalar(select(AgentHeartbeatRecord).where(
            AgentHeartbeatRecord.tenant_id == scope.tenant_id, AgentHeartbeatRecord.agent_id == scope.agent_id))
        if row is None:
            raise NotFound("Heartbeat configuration is unavailable")
        return _view(row)

    async def due(self, *, now: datetime, not_before: datetime, limit: int = 100,
            after_id: UUID | None = None) -> HeartbeatDuePage:
        _bound(limit)
        now, not_before = _aware(now), _aware(not_before)
        if not_before > now:
            raise InvalidInput("Heartbeat scan window is invalid")
        query = select(AgentHeartbeatRecord).where(AgentHeartbeatRecord.enabled.is_(True))
        if after_id is not None:
            query = query.where(AgentHeartbeatRecord.id > after_id)
        rows = (await self._session.scalars(query.order_by(AgentHeartbeatRecord.id).limit(limit))).all()
        candidates, errors = [], []
        for row in rows:
            try:
                candidates.append((row, _scheduled(row, now, not_before)))
            except InvalidInput:
                errors.append(HeartbeatDueError(row.tenant_id, row.agent_id, row.id, "invalid_configuration"))
        identities = [(row.id, at.isoformat()) for row, at in candidates if at is not None]
        existing = set((await self._session.execute(select(HeartbeatOccurrenceRecord.heartbeat_id, HeartbeatOccurrenceRecord.source_key)
            .where(tuple_(HeartbeatOccurrenceRecord.heartbeat_id, HeartbeatOccurrenceRecord.source_key).in_(identities)))).all()) if identities else set()
        return HeartbeatDuePage(tuple(HeartbeatDue(_view(row), at, at.isoformat()) for row, at in candidates
                if at is not None and (row.id, at.isoformat()) not in existing),
            rows[-1].id if len(rows) == limit else None, tuple(errors))

    async def accept(self, *, tenant_id: UUID, heartbeat_id: UUID, now: datetime,
            not_before: datetime, source_key: str, due_at: datetime) -> HeartbeatOccurrence:
        now, not_before, due_at = _aware(now), _aware(not_before), _aware(due_at)
        row = await self._session.scalar(select(AgentHeartbeatRecord).where(
            AgentHeartbeatRecord.tenant_id == tenant_id, AgentHeartbeatRecord.id == heartbeat_id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Heartbeat configuration is unavailable")
        existing_id = await self._session.scalar(select(HeartbeatOccurrenceRecord.id).where(
            HeartbeatOccurrenceRecord.tenant_id == tenant_id, HeartbeatOccurrenceRecord.heartbeat_id == heartbeat_id,
            HeartbeatOccurrenceRecord.source_key == source_key))
        if existing_id is not None:
            return _occurrence(await self._require_occurrence(tenant_id, existing_id))
        at = _scheduled(row, now, not_before)
        if not row.enabled or at != due_at or source_key != due_at.isoformat():
            raise Conflict("Heartbeat occurrence is not currently due")
        agent = await AgentService(self._tx).get_metadata(tenant_id=tenant_id, agent_id=row.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Heartbeat Agent is unavailable")
        config = _config(row)
        payload = {"instruction": config.instruction, "delegated_connections": row.delegated_connections}
        payload.update(_destination_payload(config))
        origin_kind, origin_id, _ = await self._origin(row.tenant_id, row.agent_id, _delegated(row.delegated_connections))
        payload.update({"origin_kind": origin_kind, "origin_id": str(origin_id), "origin_conversation_id": None})
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 256 * 1024:
            raise InvalidInput("Heartbeat occurrence input is too large")
        occurrence = HeartbeatOccurrenceRecord(id=uuid4(), tenant_id=tenant_id, heartbeat_id=heartbeat_id,
            agent_id=row.agent_id, source_key=source_key, due_at=at, payload_version=3,
            payload=payload,
            run_id=None, admission="pending", admission_error=None, result_version=1, result=None,
            created_at=now, updated_at=now)
        self._session.add(occurrence)
        await self._session.flush()
        return _occurrence(occurrence)

    async def get_occurrence(self, *, tenant_id: UUID, occurrence_id: UUID) -> HeartbeatOccurrence:
        value = _occurrence(await self._require_occurrence(tenant_id, occurrence_id))
        if value.origin_kind is None:
            message_id = UUID(value.source_key[len("message:"):]) if value.source_key.startswith("message:") else None
            kind, identity, conversation = await self._origin(tenant_id, value.agent_id, value.delegated_connection_ids,
                run_id=value.run_id, message_id=message_id)
            return replace(value, origin_kind=kind, origin_id=identity, origin_conversation_id=conversation)
        return value

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

    async def history(self, principal: TenantPrincipal, *, agent_id: UUID, limit: int = 100,
            after_id: UUID | None = None, max_bytes: int = 1024 * 1024) -> HeartbeatHistoryPage:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        _bound(limit)
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 16 * 1024 * 1024:
            raise InvalidInput("Heartbeat history byte bound is invalid")
        query = select(HeartbeatOccurrenceRecord).where(HeartbeatOccurrenceRecord.tenant_id == principal.tenant_id,
            HeartbeatOccurrenceRecord.agent_id == agent_id)
        if after_id is not None:
            query = query.where(HeartbeatOccurrenceRecord.id > after_id)
        sizes = (await self._session.execute(query.with_only_columns(HeartbeatOccurrenceRecord.id,
            func.octet_length(cast(HeartbeatOccurrenceRecord.payload, Text)), func.octet_length(cast(HeartbeatOccurrenceRecord.result, Text)),
            HeartbeatOccurrenceRecord.payload_version, HeartbeatOccurrenceRecord.run_id, HeartbeatOccurrenceRecord.source_key, HeartbeatOccurrenceRecord.agent_id,
            func.left(HeartbeatOccurrenceRecord.payload["origin_kind"].as_string(), 32),
            func.left(HeartbeatOccurrenceRecord.payload["origin_id"].as_string(), 64),
            func.left(HeartbeatOccurrenceRecord.payload["origin_conversation_id"].as_string(), 64))
            .order_by(HeartbeatOccurrenceRecord.id).limit(limit + 1))).all()
        for metadata in sizes[:limit]:
            if metadata[3] not in (1, 2, 3) or metadata[1] > 256 * 1024 + 4096 or (metadata[2] or 0) > 8192:
                raise InvalidInput("Stored Heartbeat occurrence exceeds its version or size boundary")
        legacy_ids = [metadata[0] for metadata in sizes[:limit] if metadata[3] < 3]
        legacy_connections: dict[UUID, tuple[UUID, ...]] = {}
        if legacy_ids:
            saved = (await self._session.execute(select(HeartbeatOccurrenceRecord.id, HeartbeatOccurrenceRecord.payload["delegated_connections"]).where(
                HeartbeatOccurrenceRecord.tenant_id == principal.tenant_id, HeartbeatOccurrenceRecord.id.in_(legacy_ids),
                func.octet_length(cast(HeartbeatOccurrenceRecord.payload["delegated_connections"], Text)) <= 8192))).all()
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
            if version == 3:
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
                    raise InvalidInput("Heartbeat occurrence cannot fit the requested page")
                break
            selected.append(id)
            used += cost
            scanned, cursor = scanned + 1, id
        rows = (await self._session.scalars(query.where(HeartbeatOccurrenceRecord.id.in_(selected),
            func.octet_length(cast(HeartbeatOccurrenceRecord.payload, Text)) <= 256 * 1024 + 4096,
            or_(HeartbeatOccurrenceRecord.result.is_(None), func.octet_length(cast(HeartbeatOccurrenceRecord.result, Text)) <= 8192))
            .order_by(HeartbeatOccurrenceRecord.id))).all() if selected else []
        if len(rows) != len(selected):
            raise InvalidInput("Heartbeat history changed during its bounded read")
        origins = {id: origin for id, _, _, origin in resolved}
        return HeartbeatHistoryPage(tuple(replace(_occurrence(row), origin_kind=origins[row.id][0],
            origin_id=origins[row.id][1], origin_conversation_id=origins[row.id][2]) for row in rows),
            cursor, len(sizes) > scanned)

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        if transaction is not self._tx:
            return await HeartbeatService(transaction).record_started(transaction, run=run)
        row = await self._from_run(run)
        if row.run_id is not None and row.run_id != run.id:
            raise Conflict("Heartbeat occurrence already has a Run")
        row.admission, row.run_id, row.admission_error = "started", run.id, None
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if transaction is not self._tx:
            return await HeartbeatService(transaction).record_outcome(transaction, run=run, outcome=outcome)
        if run.status != outcome.status:
            raise InvalidInput("Heartbeat outcome must match the terminal Run")
        row = await self._from_run(run)
        if row.run_id != run.id:
            raise Conflict("Heartbeat result has no started Run")
        result = {"run_id": str(run.id), "status": outcome.status, "reason": outcome.reason[:512] if outcome.reason else None,
            "output_preview": outcome.output[:512], "output_truncated": len(outcome.output) > 512}
        if row.result is not None and row.result != result:
            raise Conflict("Heartbeat outcome is immutable")
        row.result = result
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def fail_admission(self, *, tenant_id: UUID, occurrence_id: UUID, reason: str) -> None:
        if not reason or len(reason.encode()) > 512:
            raise InvalidInput("Heartbeat admission failure is invalid")
        row = await self._require_occurrence(tenant_id, occurrence_id, lock=True)
        if row.run_id is None:
            row.admission, row.admission_error = "failed", reason
            row.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def _from_run(self, run: RunView) -> HeartbeatOccurrenceRecord:
        if run.source.kind != "heartbeat" or run.parent_run_id is not None:
            raise InvalidInput("Heartbeat requires its own Main Run source")
        row = await self._require_occurrence(run.tenant_id, run.source.owner_id, lock=True)
        if row.agent_id != run.agent_id or row.source_key != run.source.key:
            raise Conflict("Heartbeat Run source differs from its occurrence")
        return row

    async def _require_occurrence(self, tenant_id: UUID, occurrence_id: UUID, *, lock: bool = False) -> HeartbeatOccurrenceRecord:
        query = select(HeartbeatOccurrenceRecord).where(HeartbeatOccurrenceRecord.tenant_id == tenant_id,
            HeartbeatOccurrenceRecord.id == occurrence_id)
        size = (await self._session.execute(query.with_only_columns(func.octet_length(cast(HeartbeatOccurrenceRecord.payload, Text)),
            func.octet_length(cast(HeartbeatOccurrenceRecord.result, Text))))).one_or_none()
        if size is None:
            raise NotFound("Heartbeat occurrence is unavailable")
        if size[0] > 256 * 1024 + 4096 or (size[1] or 0) > 8192:
            raise InvalidInput("Stored Heartbeat occurrence exceeds its bound")
        query = query.where(func.octet_length(cast(HeartbeatOccurrenceRecord.payload, Text)) <= 256 * 1024 + 4096,
            or_(HeartbeatOccurrenceRecord.result.is_(None), func.octet_length(cast(HeartbeatOccurrenceRecord.result, Text)) <= 8192))
        row = await self._session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if row is None:
            raise NotFound("Heartbeat occurrence is unavailable")
        _occurrence(row)
        return row
