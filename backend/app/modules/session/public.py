"""Session inputs, explicit messages and execution associations; Run owns execution."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import (
    InputContent,
    InputReference,
    RunService,
    RunView,
    TerminalOutcomePayload,
    WaitingPayload,
)
from app.modules.session.attachments import (
    SessionAttachmentBlob,
    SessionAttachmentDelegation,
    SessionAttachmentObject,
    SessionAttachmentService,
    SessionAttachmentStorage,
    SessionAttachmentView,
)
from app.modules.session.models import SessionEntryRecord, SessionRecord, SessionRunLinkRecord
from app.modules.session.repository import MAX_ENTRY_BYTES, MAX_GOAL_BYTES, MAX_PAGE_BYTES, SessionRepository
from app.modules.tool.public import (
    EnabledSources,
    PersonalAccountSelection,
    ToolService,
    decode_personal_selections,
    encode_personal_selections,
)

__all__ = ["SessionAttachmentBlob", "SessionAttachmentDelegation", "SessionAttachmentObject",
    "SessionAttachmentService", "SessionAttachmentStorage", "SessionAttachmentView"]


class _Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    reference: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=512)
    media_type: str | None = Field(default=None, max_length=256)


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    text: str
    references: list[_Reference] = Field(default_factory=list, max_length=64)
    step_id: str | None = Field(default=None, max_length=256)
    call_id: str | None = Field(default=None, max_length=256)
    waiting_reference: str | None = Field(default=None, max_length=512)
    account_selections: dict[str, object] = Field(default_factory=lambda: encode_personal_selections(()))


class _GoalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    objective: str = Field(min_length=1, max_length=8192)
    progress: str = Field(default="", max_length=8192)
    history_cutoff: int = Field(gt=0)
    current_link_id: str = Field(min_length=36, max_length=36)
    due_at: str | None = Field(default=None, max_length=64)
    scheduled_at: str
    stopped_reason: str | None = Field(default=None, max_length=512)


class _GoalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    disposition: Literal["continue", "wait", "achieved"]
    progress: str = Field(max_length=8192)
    wake_at: str | None = Field(default=None, max_length=64)


@dataclass(frozen=True, slots=True)
class GoalView:
    tenant_id: UUID
    session_id: UUID
    membership_id: UUID
    agent_id: UUID
    input_id: UUID
    enabled: bool
    objective: str
    progress: str
    history_cutoff: int
    current_link_id: UUID
    due_at: datetime | None
    scheduled_at: datetime
    stopped_reason: str | None


@dataclass(frozen=True, slots=True)
class GoalDuePage:
    goals: tuple[GoalView, ...]
    next_after_id: UUID | None
    has_more: bool
    invalid_session_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class GoalCancellation:
    goal: GoalView | None
    active_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class SessionView:
    id: UUID
    tenant_id: UUID
    membership_id: UUID
    agent_id: UUID
    through_position: int
    created_at: datetime
    updated_at: datetime
    goal_enabled: bool


@dataclass(frozen=True, slots=True)
class SessionEntryView:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    agent_id: UUID
    position: int
    kind: Literal["input", "reply"]
    content: InputContent
    source_key: str | None
    message_key: str | None
    origin_input_id: UUID | None
    source_run_id: UUID | None
    related_waiting_run_id: UUID | None
    waiting_reference: str | None
    step_id: str | None
    call_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionExecutionResult:
    """Index of a committed Run outcome; complete output remains in Run History."""
    run_id: UUID
    status: Literal["Completed", "Failed", "Cancelled", "Interrupted"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class SessionRunLink:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    agent_id: UUID
    input_id: UUID
    source_key: str
    history_cutoff: int
    run_id: UUID | None
    admission: Literal["pending", "started", "failed"]
    admission_error: str | None
    result: SessionExecutionResult | None


@dataclass(frozen=True, slots=True)
class AcceptedInput:
    entry: SessionEntryView
    link: SessionRunLink | None
    created: bool


@dataclass(frozen=True, slots=True)
class MessageAccepted:
    entry: SessionEntryView
    created: bool


@dataclass(frozen=True, slots=True)
class SessionPage:
    sessions: tuple[SessionView, ...]
    next_after_id: UUID | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class SessionHistoryPage:
    entries: tuple[SessionEntryView, ...]
    through_position: int
    next_after_position: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class SessionWorkPage:
    work: tuple[SessionRunLink, ...]
    next_after_id: UUID | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class SessionExecutionContext:
    session: SessionView
    link: SessionRunLink


@dataclass(frozen=True, slots=True)
class GoalContext:
    goal: GoalView
    input: SessionEntryView
    link: SessionRunLink


@dataclass(frozen=True, slots=True)
class SessionContextEntry:
    id: UUID
    position: int
    kind: Literal["input", "reply"]
    content: InputContent | None
    reference_only: bool


@dataclass(frozen=True, slots=True)
class SessionContextHistory:
    entries: tuple[SessionContextEntry, ...]
    through_position: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class SessionDeliveryScope:
    tenant_id: UUID
    session_id: UUID
    agent_id: UUID
    membership_id: UUID


@dataclass(frozen=True, slots=True)
class SessionHistoryFragment:
    entry_id: UUID
    position: int
    kind: Literal["input", "reply"]
    content_json: str
    next_offset: int | None
    next_after_position: int
    through_position: int


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 100:
        raise InvalidInput("Page size must be between 1 and 100")


def _source(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise InvalidInput("Source key must contain 1 to 512 characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidInput("Source key must be valid Unicode") from None


def _input_link_key(value: str) -> str:
    return "input:" + sha256(value.encode("utf-8")).hexdigest()


def _instant(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidInput("Goal scheduling requires a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        _instant(parsed)
        return parsed.astimezone(UTC)
    except (ValueError, TypeError):
        raise InvalidInput("Goal schedule is invalid") from None


def _goal_config(row: SessionRecord) -> _GoalConfig | None:
    if row.goal_configuration_version != 1:
        raise InvalidInput("Goal configuration version is unsupported")
    if row.goal_input_id is None and not row.goal_enabled and row.goal_configuration == {}:
        return None
    try:
        config = _GoalConfig.model_validate(row.goal_configuration)
        if len(config.model_dump_json().encode()) > MAX_GOAL_BYTES:
            raise ValueError("Goal configuration exceeds its bound")
        if row.goal_input_id is None or str(UUID(config.current_link_id)) != config.current_link_id:
            raise ValueError("invalid Goal references")
        if _instant(_parse_instant(config.scheduled_at)) != config.scheduled_at:
            raise ValueError("Goal schedule is not canonical UTC")
        if config.due_at is not None and _instant(_parse_instant(config.due_at)) != config.due_at:
            raise ValueError("Goal due time is not canonical UTC")
        return config
    except (ValidationError, ValueError):
        raise InvalidInput("Stored Goal configuration is invalid") from None


def _goal_view(row: SessionRecord, config: _GoalConfig) -> GoalView:
    assert row.goal_input_id is not None
    return GoalView(row.tenant_id, row.id, row.membership_id, row.agent_id, row.goal_input_id, row.goal_enabled,
        config.objective, config.progress, config.history_cutoff, UUID(config.current_link_id),
        _parse_instant(config.due_at) if config.due_at else None, _parse_instant(config.scheduled_at), config.stopped_reason)


def _store_goal(row: SessionRecord, config: _GoalConfig) -> None:
    if len(config.model_dump_json().encode()) > MAX_GOAL_BYTES:
        raise InvalidInput("Goal configuration exceeds its bound")
    row.goal_configuration = config.model_dump()
    row.updated_at = datetime.now(UTC)


def _encode(content: InputContent, *, step_id: str | None = None, call_id: str | None = None,
            waiting_reference: str | None = None,
            account_selections: tuple[PersonalAccountSelection, ...] = ()) -> dict[str, object]:
    if len(content.text) > MAX_ENTRY_BYTES or len(content.references) > 64:
        raise InvalidInput("Session content exceeds its bound")
    try:
        payload = _Payload(text=content.text, references=[_Reference(reference=ref.reference, name=ref.name,
            media_type=ref.media_type) for ref in content.references], step_id=step_id, call_id=call_id,
            waiting_reference=waiting_reference, account_selections=encode_personal_selections(account_selections))
        strings = [payload.text, payload.step_id, payload.call_id, payload.waiting_reference,
            *(value for ref in payload.references for value in (ref.reference, ref.name, ref.media_type))]
        if any(value is not None and "\x00" in value for value in strings):
            raise InvalidInput("Session content contains unsupported null characters")
        if not payload.text.strip() and not payload.references:
            raise InvalidInput("Session content requires text or an explicit reference")
        encoded = payload.model_dump_json().encode()
        if len(encoded) > MAX_ENTRY_BYTES:
            raise InvalidInput("Session content exceeds its bound")
        return payload.model_dump()
    except (ValidationError, UnicodeError):
        raise InvalidInput("Session content is invalid") from None


def _session(row: SessionRecord) -> SessionView:
    if row.goal_configuration_version != 1:
        raise InvalidInput("Session configuration version is unsupported")
    return SessionView(row.id, row.tenant_id, row.membership_id, row.agent_id, row.next_position - 1,
        row.created_at, row.updated_at, row.goal_enabled)


def _entry(row: SessionEntryRecord) -> SessionEntryView:
    if row.payload_version != 1 or row.kind not in ("input", "reply"):
        raise InvalidInput("Session entry version or kind is unsupported")
    try:
        payload = _Payload.model_validate(row.payload)
        decode_personal_selections(payload.account_selections)
        if len(payload.model_dump_json().encode()) > MAX_ENTRY_BYTES:
            raise InvalidInput("Session entry exceeds its bound")
    except (ValidationError, UnicodeError):
        raise InvalidInput("Stored Session entry is invalid") from None
    return SessionEntryView(row.id, row.tenant_id, row.session_id, row.agent_id, row.position, cast(Literal["input", "reply"], row.kind),
        InputContent(payload.text, tuple(InputReference(ref.reference, ref.name, ref.media_type) for ref in payload.references)),
        row.source_key, row.message_key, row.origin_input_id, row.source_run_id, row.related_waiting_run_id,
        row.waiting_reference or payload.waiting_reference, payload.step_id, payload.call_id, row.created_at)


def _link(row: SessionRunLinkRecord) -> SessionRunLink:
    if row.result_version != 1 or row.admission not in ("pending", "started", "failed"):
        raise InvalidInput("Session association version or admission is unsupported")
    result = None
    if row.result is not None:
        data = row.result
        if not isinstance(data, dict) or set(data) != {"run_id", "status", "reason"} or data["status"] not in ("Completed", "Failed", "Cancelled", "Interrupted"):
            raise InvalidInput("Stored Session result index is invalid")
        reason = data["reason"]
        if reason is not None and (not isinstance(reason, str) or len(reason) > 512):
            raise InvalidInput("Stored Session result index is invalid")
        try:
            result_run = UUID(data["run_id"])
        except (ValueError, TypeError, AttributeError):
            raise InvalidInput("Stored Session result Run is invalid") from None
        if result_run != row.run_id:
            raise InvalidInput("Stored Session result Run differs from its association")
        result = SessionExecutionResult(result_run, data["status"], reason)
    return SessionRunLink(row.id, row.tenant_id, row.session_id, row.agent_id, row.input_id, row.source_key,
        row.history_cutoff, row.run_id, cast(Literal["pending", "started", "failed"], row.admission), row.admission_error, result)


class SessionService:
    """All writes flush the caller's transaction; none schedules or executes a Run."""
    def __init__(self, transaction: TransactionContext, *, enabled_sources: EnabledSources | None = None) -> None:
        self._tx = transaction
        self._repository = SessionRepository(transaction)
        self._enabled_sources = enabled_sources

    async def _human(self, principal: TenantPrincipal, session_id: UUID, *, lock: bool = False) -> SessionRecord:
        row = await self._repository.get(principal.tenant_id, session_id, lock=lock)
        if row.membership_id != principal.membership_id:
            raise AccessDenied("Session belongs to another membership")
        if not principal.can_manage_all_agents and row.agent_id not in principal.allowed_agent_ids:
            raise AccessDenied("Agent is outside the captured login scope")
        return row

    async def create(self, principal: TenantPrincipal, *, agent_id: UUID) -> SessionView:
        await AgentService(self._tx).get_for_execution(principal, agent_id=agent_id)
        now = datetime.now(UTC)
        row = SessionRecord(id=uuid4(), tenant_id=principal.tenant_id, membership_id=principal.membership_id,
            agent_id=agent_id, next_position=1, goal_enabled=False, goal_input_id=None,
            goal_configuration_version=1, goal_configuration={}, created_at=now, updated_at=now)
        self._tx.session.add(row)
        await self._tx.session.flush()
        return _session(row)

    async def get(self, principal: TenantPrincipal, *, session_id: UUID) -> SessionView:
        return _session(await self._human(principal, session_id))

    async def list(self, principal: TenantPrincipal, *, after_id: UUID | None = None, limit: int = 100) -> SessionPage:
        _limit(limit)
        rows = await self._repository.list(principal.tenant_id, principal.membership_id,
            agents=None if principal.can_manage_all_agents else principal.allowed_agent_ids, after_id=after_id, limit=limit)
        selected = rows[:limit]
        return SessionPage(tuple(_session(row) for row in selected), selected[-1].id if selected else None, len(rows) > limit)

    async def accept_input(self, principal: TenantPrincipal, *, session_id: UUID, source_key: str, input: InputContent,
                           reply_to_run_id: UUID | None = None, waiting_reference: str | None = None,
                           account_selections: tuple[PersonalAccountSelection, ...] = ()) -> AcceptedInput:
        _source(source_key)
        payload = _encode(input)
        if (reply_to_run_id is None) != (waiting_reference is None):
            raise InvalidInput("An explicit Waiting reply requires both Run and reference")
        if reply_to_run_id is not None:
            owned_session = await self._human(principal, session_id)
            retry = await self._repository.entry_by_key(principal.tenant_id, session_id, key=source_key)
            if retry is not None:
                previous_link = await self._repository.link(principal.tenant_id, session_id, source_key=_input_link_key(source_key))
                return AcceptedInput(_entry(retry), _link(previous_link) if previous_link else None, False)
            owned_target = await self._repository.link(principal.tenant_id, session_id, run_id=reply_to_run_id)
            if owned_target is None or owned_target.agent_id != owned_session.agent_id:
                raise AccessDenied("Waiting Run is not associated with this Session")
            # The entry's Run foreign key also takes a database lock: acquire the
            # Run first, matching terminal consumers' Run-before-Session order.
            target = await RunService(self._tx).lock_main(tenant_id=principal.tenant_id, run_id=reply_to_run_id)
            if target.parent_run_id is not None or target.status != "Waiting" or target.waiting_reference != waiting_reference:
                raise Conflict("The selected Run is not waiting for this reply")
        # No new Run lock is acquired after this Session append-position lock.
        row = await self._human(principal, session_id, lock=True)
        existing = await self._repository.entry_by_key(principal.tenant_id, session_id, key=source_key)
        if existing is not None:
            linked = await self._repository.link(principal.tenant_id, session_id, source_key=_input_link_key(source_key))
            return AcceptedInput(_entry(existing), _link(linked) if linked else None, False)
        if account_selections:
            if reply_to_run_id is not None:
                raise InvalidInput("Account authorization requires a new request, not a reply to a fixed Run")
            await ToolService(self._tx, enabled_sources=self._enabled_sources).validate_personal_selections(
                principal, selections=account_selections)
            payload = _encode(input, account_selections=account_selections)
        if reply_to_run_id is not None:
            assert waiting_reference is not None
            _source(waiting_reference)
            link = await self._repository.link(principal.tenant_id, session_id, run_id=reply_to_run_id)
            if link is None or link.agent_id != row.agent_id:
                raise AccessDenied("Waiting Run is not associated with this Session")
        now = datetime.now(UTC)
        entry = SessionEntryRecord(id=uuid4(), tenant_id=row.tenant_id, session_id=row.id, agent_id=row.agent_id,
            position=row.next_position, kind="input", source_key=source_key, message_key=None, source_run_id=None,
            origin_input_id=None, related_waiting_run_id=reply_to_run_id, waiting_reference=waiting_reference,
            payload_version=1, payload=payload, created_at=now, updated_at=now)
        row.next_position += 1
        row.updated_at = now
        self._tx.session.add(entry)
        await self._tx.session.flush()
        pending = None
        if reply_to_run_id is None:
            pending = SessionRunLinkRecord(id=uuid4(), tenant_id=row.tenant_id, session_id=row.id, agent_id=row.agent_id,
                input_id=entry.id, source_key=_input_link_key(source_key), history_cutoff=entry.position, run_id=None,
                admission="pending", admission_error=None, result_version=1, result=None, created_at=now, updated_at=now)
            self._tx.session.add(pending)
            await self._tx.session.flush()
        return AcceptedInput(_entry(entry), _link(pending) if pending else None, True)

    async def read_history(self, principal: TenantPrincipal, *, session_id: UUID, after_position: int = 0,
                           through_position: int | None = None, limit: int = 100, max_bytes: int = MAX_PAGE_BYTES) -> SessionHistoryPage:
        _limit(limit)
        row = await self._human(principal, session_id)
        through = row.next_position - 1 if through_position is None else through_position
        if (type(after_position) is not int or type(through) is not int or not 0 <= after_position <= through < row.next_position
                or type(max_bytes) is not int or not 4096 <= max_bytes <= MAX_PAGE_BYTES):
            raise InvalidInput("Session history cursor or byte bound is invalid")
        entries = await self._repository.entries(row.tenant_id, row.id, after=after_position, through=through, limit=limit, max_bytes=max_bytes)
        next_position = entries[-1].position if entries else after_position
        return SessionHistoryPage(tuple(_entry(entry) for entry in entries), through, next_position, next_position < through)

    async def read_delivery_page(self, *, tenant_id: UUID, session_id: UUID, agent_id: UUID, membership_id: UUID,
            after_position: int, limit: int = 100) -> SessionHistoryPage:
        """Trusted Channel conversation mapping; include every position so consumers cannot miss gaps."""
        _limit(limit)
        row = await self._repository.get(tenant_id, session_id)
        if (row.agent_id, row.membership_id) != (agent_id, membership_id):
            raise AccessDenied("Channel conversation does not match its Session owner")
        through = row.next_position - 1
        if type(after_position) is not int or not 0 <= after_position <= through:
            raise InvalidInput("Channel Session cursor is invalid")
        entries = await self._repository.entries(tenant_id, session_id, after=after_position, through=through,
            limit=limit, max_bytes=1024 * 1024)
        after = entries[-1].position if entries else after_position
        return SessionHistoryPage(tuple(_entry(entry) for entry in entries), through, after, after < through)

    async def delivery_heads(self, scopes: tuple[SessionDeliveryScope, ...]) -> dict[UUID, int]:
        if not isinstance(scopes, tuple) or len(scopes) > 100:
            raise InvalidInput("Session delivery scope batch is invalid")
        if not scopes:
            return {}
        from sqlalchemy import select, tuple_
        keys = {(scope.tenant_id, scope.session_id, scope.agent_id, scope.membership_id) for scope in scopes}
        rows = (await self._tx.session.execute(select(SessionRecord.id, SessionRecord.next_position).where(
            tuple_(SessionRecord.tenant_id, SessionRecord.id, SessionRecord.agent_id, SessionRecord.membership_id).in_(keys)))).all()
        if len(rows) != len(keys):
            raise AccessDenied("Channel Session scopes do not match their owners")
        return {id: position - 1 for id, position in rows}

    async def get_input(self, principal: TenantPrincipal, *, session_id: UUID, input_id: UUID) -> SessionEntryView:
        await self._human(principal, session_id)
        row = await self._repository.entry(principal.tenant_id, session_id, input_id)
        if row.kind != "input":
            raise InvalidInput("Session entry is not a human input")
        return _entry(row)

    async def input_accounts(self, principal: TenantPrincipal, *, session_id: UUID, input_id: UUID,
            target_agent_id: UUID) -> tuple[UUID, ...]:
        await self._human(principal, session_id)
        row = await self._repository.entry(principal.tenant_id, session_id, input_id)
        if row.kind != "input":
            raise InvalidInput("Account selection requires its human input")
        return self._accounts(row, target_agent_id)

    async def execution_accounts(self, run: RunView, *, target_agent_id: UUID) -> tuple[UUID, ...]:
        """Only this Run's original human input grants accounts; history and replies cannot expand it."""
        context = await self.get_execution_context(run)
        row = await self._repository.entry(run.tenant_id, context.session.id, context.link.input_id)
        return self._accounts(row, target_agent_id)

    @staticmethod
    def _accounts(row: SessionEntryRecord, target_agent_id: UUID) -> tuple[UUID, ...]:
        _entry(row)
        payload = _Payload.model_validate(row.payload)
        return next((item.connection_ids for item in decode_personal_selections(payload.account_selections)
            if item.target_agent_id == target_agent_id), ())

    async def read_context_history(self, principal: TenantPrincipal, *, session_id: UUID, through_position: int,
                                   limit: int = 20, max_bytes: int = 16384) -> SessionContextHistory:
        _limit(limit)
        session = await self._human(principal, session_id)
        if (type(through_position) is not int or not 0 <= through_position < session.next_position
                or type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_PAGE_BYTES):
            raise InvalidInput("Context history cutoff or byte bound is invalid")
        metadata = await self._repository.context_tail(principal.tenant_id, session_id, through=through_position, limit=limit)
        if (not metadata and through_position) or any(item.position != through_position - index for index, item in enumerate(metadata)):
            raise InvalidInput("Stored Context history is incomplete")
        used = 512
        selected = []
        for item in metadata[:limit]:
            if item.kind not in ("input", "reply") or item.size > MAX_ENTRY_BYTES + 4096:
                raise InvalidInput("Stored Context history is invalid")
            if used + 256 > max_bytes:
                break
            include = used + 256 + item.size <= max_bytes
            if include:
                used += item.size
            selected.append((item, include))
            used += 256
        rows = await self._repository.selected_entries(principal.tenant_id, session_id, tuple(item.id for item, include in selected if include))
        values = [SessionContextEntry(item.id, item.position, cast(Literal["input", "reply"], item.kind),
            _entry(rows[item.id]).content if include else None, not include) for item, include in selected]
        values.reverse()
        return SessionContextHistory(tuple(values), through_position, bool(values and values[0].position > 1) or len(metadata) > len(values))

    async def get_link(self, principal: TenantPrincipal, *, session_id: UUID, link_id: UUID) -> SessionRunLink:
        await self._human(principal, session_id)
        row = await self._repository.link(principal.tenant_id, session_id, link_id=link_id)
        if row is None:
            raise NotFound("Session work association is unavailable")
        return _link(row)

    async def list_work(self, principal: TenantPrincipal, *, session_id: UUID, after_id: UUID | None = None, limit: int = 100) -> SessionWorkPage:
        _limit(limit)
        await self._human(principal, session_id)
        rows = await self._repository.links(principal.tenant_id, session_id, after_id=after_id, limit=limit)
        selected = rows[:limit]
        return SessionWorkPage(tuple(_link(row) for row in selected), selected[-1].id if selected else None, len(rows) > limit)

    async def admission_failed(self, principal: TenantPrincipal, *, session_id: UUID, link_id: UUID, reason: str) -> SessionRunLink:
        session = await self._human(principal, session_id, lock=True)
        row = await self._repository.link(principal.tenant_id, session_id, link_id=link_id, lock=True)
        if row is None:
            raise NotFound("Session work association is unavailable")
        if row.admission != "started":
            row.admission, row.admission_error, row.updated_at = "failed", reason[:512], datetime.now(UTC)
            if session.goal_enabled and session.goal_input_id == row.input_id:
                goal_row = await self._repository.goal_session(principal.tenant_id, session_id)
                goal = _goal_config(goal_row)
                if goal is not None and goal.current_link_id == str(link_id):
                    goal_row.goal_enabled = False
                    _store_goal(goal_row, goal.model_copy(update={"due_at": None, "stopped_reason": "admission_failed"}))
            await self._tx.session.flush()
        return _link(row)

    async def _run_link(self, run: RunView) -> tuple[SessionRecord, SessionRunLinkRecord]:
        if run.parent_run_id is not None or run.source.kind != "session":
            raise AccessDenied("This execution has no direct Session destination")
        try:
            link_id = UUID(run.source.key)
        except ValueError:
            raise InvalidInput("Session execution source is invalid") from None
        if str(link_id) != run.source.key:
            raise InvalidInput("Session execution source must use its canonical association ID")
        row = await self._repository.get(run.tenant_id, run.source.owner_id, lock=True)
        link = await self._repository.link(run.tenant_id, row.id, link_id=link_id, lock=True)
        if link is None or (row.agent_id, link.agent_id) != (run.agent_id, run.agent_id):
            raise AccessDenied("Execution source does not match its Session association")
        if link.run_id is not None and link.run_id != run.id:
            raise Conflict("Session association already belongs to another Run")
        return row, link

    async def _message(self, run: RunView, *, key: str, content: InputContent, step_id: str,
                       call_id: str | None, waiting_reference: str | None = None) -> MessageAccepted:
        row, link = await self._run_link(run)
        if link.run_id != run.id or link.admission != "started":
            raise Conflict("Session Run is not associated with committed startup")
        existing = await self._repository.entry_by_key(run.tenant_id, row.id, key=key, message=True)
        if existing is not None:
            return MessageAccepted(_entry(existing), False)
        payload = _encode(content, step_id=step_id, call_id=call_id, waiting_reference=waiting_reference)
        now = datetime.now(UTC)
        entry = SessionEntryRecord(id=uuid4(), tenant_id=row.tenant_id, session_id=row.id, agent_id=row.agent_id,
            position=row.next_position, kind="reply", source_key=None, message_key=key, source_run_id=run.id,
            origin_input_id=link.input_id, related_waiting_run_id=None, waiting_reference=None,
            payload_version=1, payload=payload, created_at=now, updated_at=now)
        row.next_position += 1
        row.updated_at = now
        self._tx.session.add(entry)
        await self._tx.session.flush()
        return MessageAccepted(_entry(entry), True)

    async def accept_message(self, *, run: RunView, step_id: str, call_id: str, input: InputContent) -> MessageAccepted:
        runs = RunService(self._tx)
        locked = await runs.lock_main(tenant_id=run.tenant_id, run_id=run.id)
        if locked.source.kind != "session":
            raise AccessDenied("This execution has no direct Session destination")
        key = "message:" + sha256(f"{run.id}\0{step_id}\0{call_id}".encode()).hexdigest()
        existing = await self._repository.entry_by_key(run.tenant_id, locked.source.owner_id, key=key, message=True)
        if existing is not None:
            if existing.source_run_id != locked.id:
                raise Conflict("Message correlation belongs to another Run")
            return MessageAccepted(_entry(existing), False)
        locked = await runs.verify_main_tool_origin(tenant_id=run.tenant_id, run_id=run.id,
            step_id=step_id, call_id=call_id, tool_name="send_message")
        return await self._message(locked, key=key, content=input, step_id=step_id, call_id=call_id)

    async def find_accepted_message(self, *, run: RunView, step_id: str, call_id: str) -> SessionEntryView | None:
        if run.parent_run_id is not None or run.source.kind != "session":
            raise AccessDenied("This execution has no Session message destination")
        key = "message:" + sha256(f"{run.id}\0{step_id}\0{call_id}".encode()).hexdigest()
        row = await self._repository.entry_by_key(run.tenant_id, run.source.owner_id, key=key, message=True)
        if row is None:
            return None
        entry = await self.get_message_for_delivery(tenant_id=run.tenant_id, agent_id=run.agent_id, message_id=row.id)
        if entry.source_run_id != run.id:
            raise Conflict("Message correlation belongs to another Run")
        return entry

    async def get_message_for_delivery(self, *, tenant_id: UUID, agent_id: UUID, message_id: UUID) -> SessionEntryView:
        """Trusted Channel orchestration reads an accepted message in its own transaction."""
        entry = await self._repository.message(tenant_id, agent_id, message_id)
        if entry.source_run_id is None or entry.origin_input_id is None:
            raise InvalidInput("Session message lacks its source association")
        link = await self._repository.link(tenant_id, entry.session_id, run_id=entry.source_run_id)
        if link is None or link.agent_id != agent_id or link.input_id != entry.origin_input_id:
            raise InvalidInput("Session message source is inconsistent")
        return _entry(entry)

    async def get_execution_context(self, run: RunView) -> SessionExecutionContext:
        actual = await RunService(self._tx).get(tenant_id=run.tenant_id, run_id=run.id)
        if actual.parent_run_id is not None or actual.source.kind != "session":
            raise AccessDenied("Only a Session Main may read this execution context")
        session = await self._repository.get(actual.tenant_id, actual.source.owner_id)
        link = await self._repository.link(actual.tenant_id, session.id, run_id=actual.id)
        if link is None or (link.agent_id, session.agent_id) != (actual.agent_id, actual.agent_id) or str(link.id) != actual.source.key:
            raise AccessDenied("Run does not match its Session association")
        return SessionExecutionContext(_session(session), _link(link))

    async def read_execution_history(self, run: RunView, *, after_position: int = 0, limit: int = 100,
                                     max_bytes: int = MAX_PAGE_BYTES) -> SessionHistoryPage:
        _limit(limit)
        context = await self.get_execution_context(run)
        through = context.link.history_cutoff
        if type(after_position) is not int or not 0 <= after_position <= through or not 4096 <= max_bytes <= MAX_PAGE_BYTES:
            raise InvalidInput("Execution history cursor or bound is invalid")
        entries = await self._repository.entries(run.tenant_id, context.session.id, after=after_position,
            through=through, limit=limit, max_bytes=max_bytes)
        next_position = entries[-1].position if entries else after_position
        return SessionHistoryPage(tuple(_entry(entry) for entry in entries), through, next_position, next_position < through)

    async def list_work_for_run(self, run: RunView, *, after_id: UUID | None = None, limit: int = 100) -> SessionWorkPage:
        _limit(limit)
        context = await self.get_execution_context(run)
        rows = await self._repository.links(run.tenant_id, context.session.id, after_id=after_id, limit=limit)
        selected = rows[:limit]
        return SessionWorkPage(tuple(_link(row) for row in selected), selected[-1].id if selected else None, len(rows) > limit)

    async def read_execution_history_fragment(self, run: RunView, *, after_position: int = 0,
            content_offset: int = 0, max_characters: int = 16000) -> SessionHistoryFragment | None:
        context = await self.get_execution_context(run)
        through = context.link.history_cutoff
        if (type(after_position) is not int or not 0 <= after_position <= through
                or type(content_offset) is not int or not 0 <= content_offset <= MAX_ENTRY_BYTES + 4096
                or type(max_characters) is not int or not 1 <= max_characters <= 16000):
            raise InvalidInput("Session fragment cursor or bound is invalid")
        value = await self._repository.fragment(run.tenant_id, context.session.id, after=after_position,
            through=through, offset=content_offset, characters=max_characters)
        if value is None:
            return None
        metadata, content = value
        next_offset = content_offset + len(content)
        done = next_offset == metadata.characters
        return SessionHistoryFragment(metadata.id, metadata.position, metadata.kind, content,
            None if done else next_offset, metadata.position if done else after_position, through)

    async def authorize_work(self, *, run: RunView, target_run_id: UUID) -> SessionRunLink:
        """Validate associations before the caller invokes Run's mutation port; no product lock is retained."""
        context = await self.get_execution_context(run)
        target = await self._repository.link(run.tenant_id, context.session.id, run_id=target_run_id)
        if target is None or target.agent_id != context.session.agent_id:
            raise AccessDenied("Work must belong to the same Session and Agent")
        return _link(target)

    async def enable_goal(self, principal: TenantPrincipal, *, session_id: UUID, input_id: UUID, objective: str) -> GoalView:
        await self._human(principal, session_id)
        row = await self._repository.goal_session(principal.tenant_id, session_id, lock=True)
        existing = _goal_config(row)
        if row.goal_enabled:
            if row.goal_input_id == input_id and existing is not None and existing.objective == objective:
                return _goal_view(row, existing)
            raise Conflict("Cancel the existing Goal before replacing it")
        entry = await self._repository.entry(principal.tenant_id, session_id, input_id)
        if entry.kind != "input" or entry.related_waiting_run_id is not None:
            raise InvalidInput("Goal requires an original Session input")
        assert entry.source_key is not None
        link = await self._repository.link(principal.tenant_id, session_id, source_key=_input_link_key(entry.source_key))
        if link is None or link.run_id is not None:
            raise Conflict("Enable Goal before starting its original input")
        try:
            config = _GoalConfig(objective=objective, history_cutoff=link.history_cutoff,
                current_link_id=str(link.id), scheduled_at=_instant(datetime.now(UTC)))
        except ValidationError:
            raise InvalidInput("Goal objective is invalid") from None
        if not objective.strip() or "\x00" in objective:
            raise InvalidInput("Goal objective must not be blank")
        row.goal_input_id, row.goal_enabled = input_id, True
        _store_goal(row, config)
        await self._tx.session.flush()
        return _goal_view(row, config)

    async def get_goal(self, principal: TenantPrincipal, *, session_id: UUID, expected_input_id: UUID | None = None) -> GoalView | None:
        session = await self._human(principal, session_id)
        if expected_input_id is not None and session.goal_input_id != expected_input_id:
            return None
        row = await self._repository.goal_session(principal.tenant_id, session_id)
        config = _goal_config(row)
        return _goal_view(row, config) if config else None

    async def get_goal_context(self, *, tenant_id: UUID, session_id: UUID, expected_link_id: UUID | None = None) -> GoalContext:
        """Trusted autonomous dispatch uses only the configured original input and Membership."""
        row = await self._repository.goal_session(tenant_id, session_id)
        config = _goal_config(row)
        if config is None or not row.goal_enabled or (expected_link_id is not None and str(expected_link_id) != config.current_link_id):
            raise Conflict("Goal is no longer eligible for this admission")
        view = _goal_view(row, config)
        link = await self._repository.link(tenant_id, session_id, link_id=view.current_link_id)
        if link is None or link.input_id != view.input_id or link.history_cutoff != view.history_cutoff or link.agent_id != view.agent_id:
            raise InvalidInput("Goal association differs from its original input")
        entry = await self._repository.entry(tenant_id, session_id, view.input_id)
        return GoalContext(view, _entry(entry), _link(link))

    async def goal_accounts(self, *, tenant_id: UUID, session_id: UUID, expected_link_id: UUID) -> tuple[UUID, ...]:
        context = await self.get_goal_context(tenant_id=tenant_id, session_id=session_id, expected_link_id=expected_link_id)
        entry = await self._repository.entry(tenant_id, session_id, context.goal.input_id)
        return self._accounts(entry, context.goal.agent_id)

    async def get_goal_for_run(self, run: RunView) -> GoalView | None:
        execution = await self.get_execution_context(run)
        session = await self._repository.get(run.tenant_id, execution.session.id)
        if session.goal_input_id != execution.link.input_id:
            return None
        row = await self._repository.goal_session(run.tenant_id, execution.session.id)
        config = _goal_config(row)
        if config is None or row.goal_input_id != execution.link.input_id or config.current_link_id != str(execution.link.id):
            return None
        return _goal_view(row, config)

    async def goal_due(self, *, now: datetime, not_before: datetime, after_session_id: UUID | None = None, limit: int = 100) -> GoalDuePage:
        _limit(limit)
        rows, invalid, next_id, has_more = await self._repository.due_goals(
            now=_instant(now), not_before=_instant(not_before), after_id=after_session_id, limit=limit)
        errors = list(invalid)
        goals = []
        for row in rows:
            try:
                config = _goal_config(row)
                if config is None:
                    raise InvalidInput("Due Goal configuration is missing")
                view = _goal_view(row, config)
            except InvalidInput:
                errors.append(row.id)
                continue
            if view.enabled and view.due_at is not None and view.due_at <= now and view.scheduled_at >= not_before:
                goals.append(view)
        return GoalDuePage(tuple(goals), next_id, has_more, tuple(errors))

    async def prepare_goal_admission(self, *, tenant_id: UUID, session_id: UUID, expected_link_id: UUID,
                                     now: datetime) -> SessionRunLink | None:
        _instant(now)
        row = await self._repository.goal_session(tenant_id, session_id, lock=True)
        config = _goal_config(row)
        if (config is None or not row.goal_enabled or config.current_link_id != str(expected_link_id)
                or config.due_at is None or _parse_instant(config.due_at) > now):
            return None
        link = await self._repository.link(tenant_id, session_id, link_id=expected_link_id, lock=True)
        if link is None or link.input_id != row.goal_input_id or link.history_cutoff != config.history_cutoff:
            raise InvalidInput("Goal admission differs from its committed input")
        if link.admission != "pending" or link.run_id is not None:
            raise Conflict("Goal association is not pending admission")
        _store_goal(row, config.model_copy(update={"due_at": None}))
        await self._tx.session.flush()
        return _link(link)

    async def fail_goal_admission(self, *, tenant_id: UUID, session_id: UUID, expected_link_id: UUID, reason: str) -> None:
        row = await self._repository.goal_session(tenant_id, session_id, lock=True)
        config = _goal_config(row)
        if config is None or not row.goal_enabled or config.current_link_id != str(expected_link_id):
            return
        link = await self._repository.link(tenant_id, session_id, link_id=expected_link_id, lock=True)
        if link is None:
            raise InvalidInput("Goal association is unavailable")
        if link.run_id is not None:
            return
        link.admission, link.admission_error, link.updated_at = "failed", reason[:512], datetime.now(UTC)
        row.goal_enabled = False
        _store_goal(row, config.model_copy(update={"due_at": None, "stopped_reason": "admission_failed"}))
        await self._tx.session.flush()

    async def cancel_goal(self, principal: TenantPrincipal, *, session_id: UUID) -> GoalCancellation:
        await self._human(principal, session_id)
        before = await self._repository.goal_session(principal.tenant_id, session_id)
        previous = _goal_config(before)
        if previous is None:
            return GoalCancellation(None, None)
        link = await self._repository.link(principal.tenant_id, session_id, link_id=UUID(previous.current_link_id))
        active = None
        if link is not None and link.run_id is not None:
            active = await RunService(self._tx).lock_main(tenant_id=principal.tenant_id, run_id=link.run_id)
        row = await self._repository.goal_session(principal.tenant_id, session_id, lock=True)
        config = _goal_config(row)
        if config is None or config.current_link_id != previous.current_link_id:
            raise Conflict("Goal iteration changed; retry cancellation")
        current_link = await self._repository.link(principal.tenant_id, session_id, link_id=UUID(config.current_link_id), lock=True)
        if current_link is None or current_link.run_id != (active.id if active is not None else None):
            raise Conflict("Goal admission changed; retry cancellation")
        row.goal_enabled = False
        stopped = config.model_copy(update={"due_at": None, "stopped_reason": "cancelled"})
        _store_goal(row, stopped)
        await self._tx.session.flush()
        return GoalCancellation(_goal_view(row, stopped),
            active.id if active is not None and active.status in ("Running", "Waiting") else None)

    async def _consume_goal_outcome(self, run: RunView, link: SessionRunLinkRecord, outcome: TerminalOutcomePayload) -> None:
        session = await self._repository.get(run.tenant_id, link.session_id, lock=True)
        if not session.goal_enabled or session.goal_input_id != link.input_id:
            return
        row = await self._repository.goal_session(run.tenant_id, link.session_id, lock=True)
        config = _goal_config(row)
        if config is None or not row.goal_enabled or row.goal_input_id != link.input_id or config.current_link_id != str(link.id):
            return
        if outcome.status != "Completed":
            row.goal_enabled = False
            _store_goal(row, config.model_copy(update={"due_at": None, "stopped_reason": outcome.status.lower()}))
            return
        now = datetime.now(UTC)
        try:
            if len(outcome.output) > MAX_GOAL_BYTES or len(outcome.output.encode()) > MAX_GOAL_BYTES:
                raise ValueError("Goal result exceeds its bound")
            raw = json.loads(outcome.output)
            if not isinstance(raw, dict) or set(raw) != {"goal"}:
                raise ValueError("Goal disposition is missing")
            decision = _GoalDecision.model_validate(raw["goal"])
            due = _parse_instant(decision.wake_at) if decision.wake_at is not None else None
            if decision.disposition == "wait" and (due is None or due <= now):
                raise ValueError("Goal wait requires a future wake time")
            if decision.disposition != "wait" and due is not None:
                raise ValueError("Only a Goal wait accepts a wake time")
            next_config = config.model_copy(update={"progress": decision.progress, "scheduled_at": _instant(now), "stopped_reason": None})
            if "\x00" in decision.progress or len(next_config.model_dump_json().encode()) > MAX_GOAL_BYTES:
                raise ValueError("Goal progress exceeds its persistence bound")
        except (ValueError, UnicodeError, RecursionError, InvalidInput):
            row.goal_enabled = False
            _store_goal(row, config.model_copy(update={"due_at": None, "stopped_reason": "malformed_goal_result"}))
            return
        if decision.disposition == "achieved":
            row.goal_enabled = False
            _store_goal(row, next_config.model_copy(update={"due_at": None, "stopped_reason": "achieved"}))
            return
        source = "goal:" + str(link.input_id) + ":" + str(run.id)
        pending = await self._repository.link(run.tenant_id, link.session_id, source_key=source)
        if pending is None:
            pending = SessionRunLinkRecord(id=uuid4(), tenant_id=link.tenant_id, session_id=link.session_id, agent_id=link.agent_id,
                input_id=link.input_id, source_key=source, history_cutoff=config.history_cutoff, run_id=None,
                admission="pending", admission_error=None, result_version=1, result=None, created_at=now, updated_at=now)
            self._tx.session.add(pending)
        _store_goal(row, next_config.model_copy(update={"current_link_id": str(pending.id), "due_at": _instant(due or now)}))


class SessionConsumers:
    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        service = SessionService(transaction)
        row, link = await service._run_link(run)
        configured = row
        goal = None
        if row.goal_input_id == link.input_id or link.source_key.startswith("goal:"):
            configured = await service._repository.goal_session(run.tenant_id, row.id)
            goal = _goal_config(configured)
        if link.source_key.startswith("goal:") and (goal is None or not configured.goal_enabled or goal.current_link_id != str(link.id)):
            raise Conflict("Goal admission is no longer current")
        if link.source_key.startswith("goal:") and goal is not None and goal.due_at is not None:
            raise Conflict("Goal admission must be prepared after its due time")
        if (goal is not None and configured.goal_input_id == link.input_id and goal.current_link_id == str(link.id)
                and not configured.goal_enabled and goal.stopped_reason == "cancelled"):
            raise Conflict("Goal was cancelled before its Run started")
        if link.run_id is not None:
            return
        link.run_id, link.admission, link.admission_error, link.updated_at = run.id, "started", None, datetime.now(UTC)
        await transaction.session.flush()

    async def record_waiting(self, transaction: TransactionContext, *, run: RunView, waiting: WaitingPayload) -> None:
        if run.status != "Waiting" or run.waiting_reference != waiting.reference or not waiting.question.strip():
            raise InvalidInput("Only an accepted human-question Waiting fact can create a Session question")
        key = "waiting:" + sha256(f"{run.id}\0{waiting.reference}".encode()).hexdigest()
        await SessionService(transaction)._message(run, key=key, content=InputContent(waiting.question),
            step_id=waiting.step_id, call_id=None, waiting_reference=waiting.reference)

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if run.status != outcome.status:
            raise InvalidInput("Session outcome must match the committed Run terminal fact")
        _, link = await SessionService(transaction)._run_link(run)
        if link.run_id != run.id:
            raise Conflict("Session outcome has no committed startup association")
        result = {"run_id": str(run.id), "status": outcome.status, "reason": outcome.reason[:512] if outcome.reason else None}
        if link.result is not None and link.result != result:
            raise Conflict("Session outcome index is immutable")
        link.result, link.updated_at = result, datetime.now(UTC)
        await SessionService(transaction)._consume_goal_outcome(run, link, outcome)
        await transaction.session.flush()
