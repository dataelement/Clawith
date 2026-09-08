"""Private Run lifecycle authority; the caller commits and schedules committed changes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, cast, get_args
from uuid import UUID

from sqlalchemy import Text, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.run.contracts import (
    HistoryKind,
    HistoryPayload,
    InitialInputPayload,
    InputContent,
    InputReference,
    InvalidHistory,
    ModelStepPayload,
    RelatedInputPayload,
    TerminalOutcomePayload,
    ToolResultPayload,
    WaitingPayload,
    encode_history,
    supported_history_version,
)
from app.modules.run.models import RunHistoryRecord, RunRecord, RunSnapshotRecord
from app.modules.run.repository import (
    MAX_STORED_PAYLOAD_BYTES,
    AppendHistoryResult,
    HistoryEntry,
    HistoryPage,
    RunHistoryRepository,
    SourceIdentity,
)
from app.modules.run.snapshot import SNAPSHOT_KIND, RunSnapshot, SnapshotRepository, _prepare_snapshot, derive_child

RunStatus = Literal["Running", "Waiting", "Completed", "Failed", "Cancelled", "Interrupted"]
_ACTIVE = ("Running", "Waiting")
MAX_TRANSACTION_RUNS = 1000


@dataclass(frozen=True, slots=True)
class HistoryFragment:
    sequence: int
    kind: HistoryKind
    version: int
    content_json_fragment: str
    next_offset: int | None
    next_after_sequence: int
    through_sequence: int


@dataclass(frozen=True, slots=True)
class RunView:
    tenant_id: UUID
    agent_id: UUID
    run_id: UUID
    parent_run_id: UUID | None
    status: RunStatus
    latest_history_sequence: int
    waiting_reference: str | None
    source: SourceIdentity

    @property
    def id(self) -> UUID:
        return self.run_id


@dataclass(frozen=True, slots=True)
class StartResult:
    run: RunView
    created: bool

    @property
    def view(self) -> RunView:
        return self.run


@dataclass(frozen=True, slots=True)
class TransitionResult:
    run: RunView
    changed: bool
    terminal_run_ids: tuple[UUID, ...] = ()
    wake_run_ids: tuple[UUID, ...] = ()
    affected: tuple[RunView, ...] = ()


class OutcomeConsumer(Protocol):
    async def record_outcome(self, transaction: TransactionContext, *, run: RunView,
            outcome: TerminalOutcomePayload) -> None: ...


def _view(row: RunRecord) -> RunView:
    if row.status not in (*_ACTIVE, "Completed", "Failed", "Cancelled", "Interrupted"):
        raise Conflict("Run status is invalid")
    return RunView(row.tenant_id, row.agent_id, row.id, row.parent_run_id,
        cast(RunStatus, row.status), row.latest_history_sequence, row.active_waiting_reference,
        SourceIdentity(row.initiator_kind, row.initiator_owner_id, row.source_key))


class RunService:
    def __init__(self, transaction: TransactionContext) -> None:
        self._transaction = transaction
        self._session = transaction.session
        self._history = RunHistoryRepository(transaction)
        self._snapshots = SnapshotRepository(transaction)

    async def get(self, *, tenant_id: UUID, run_id: UUID) -> RunView:
        return _view(await self._row(tenant_id, run_id))

    async def find_by_source(self, *, tenant_id: UUID, source: SourceIdentity) -> RunView | None:
        row = await self._session.scalar(select(RunRecord).where(RunRecord.tenant_id == tenant_id,
            RunRecord.initiator_kind == source.kind, RunRecord.initiator_owner_id == source.owner_id,
            RunRecord.source_key == source.key).execution_options(populate_existing=True))
        return _view(row) if row is not None else None

    async def verify_task_origin(self, *, tenant_id: UUID, parent_run_id: UUID,
            step_id: str, call_id: str) -> None:
        row = await self._row(tenant_id, parent_run_id)
        if row.parent_run_id is not None or row.status != "Running":
            raise InvalidInput("Task operations require a Running Main Run")
        step = await self._step(row, step_id)
        if not any(call.call_id == call_id and call.name == "task" for call in step.result.calls):
            raise InvalidInput("Task operation requires its originating Model Tool call")
        snapshot = await self.read_snapshot(tenant_id=tenant_id, run_id=parent_run_id)
        if not any(tool.definition.spec.name == "task" for tool in snapshot.tools.for_role("main").tools):
            raise InvalidInput("Task is not in this Run's captured authorization")

    async def read_snapshot(self, *, tenant_id: UUID, run_id: UUID) -> RunSnapshot:
        return await self._snapshots.read(tenant_id=tenant_id, run_id=run_id)

    async def read_history(self, *, tenant_id: UUID, run_id: UUID, after_sequence: int = 0,
            through_sequence: int | None = None, limit: int = 100, max_bytes: int = 32 * 1024 * 1024) -> HistoryPage:
        return await self._history.read_page(tenant_id=tenant_id, run_id=run_id, after_sequence=after_sequence,
            through_sequence=through_sequence, limit=limit, max_bytes=max_bytes)

    async def latest_fact(self, *, tenant_id: UUID, run_id: UUID, kind: HistoryKind,
            successful_tool_name: str | None = None) -> HistoryEntry | None:
        """Bounded bootstrap lookup for the newest base, exposure, or planning observation."""
        await self._row(tenant_id, run_id)
        if kind not in ("context_base", "model_input", "tool_result"):
            raise InvalidInput("Unsupported latest fact lookup")
        query = select(RunHistoryRecord.sequence).where(RunHistoryRecord.tenant_id == tenant_id,
            RunHistoryRecord.run_id == run_id, RunHistoryRecord.payload_kind == kind)
        if successful_tool_name is not None:
            if kind != "tool_result" or successful_tool_name != "todo":
                raise InvalidInput("Unsupported planning fact lookup")
            query = query.where(RunHistoryRecord.payload["tool_name"].as_string() == successful_tool_name,
                RunHistoryRecord.payload["result"]["status"].as_string() == "success")
        sequence = await self._session.scalar(query.order_by(RunHistoryRecord.sequence.desc()).limit(1))
        if sequence is None:
            return None
        page = await self._history.read_page(tenant_id=tenant_id, run_id=run_id,
            after_sequence=sequence - 1, through_sequence=sequence, limit=1)
        return page.entries[0]

    async def read_history_fragment(self, *, tenant_id: UUID, run_id: UUID, after_sequence: int = 0,
            content_offset: int = 0, max_characters: int = 16000) -> HistoryFragment | None:
        """Inspect one immutable raw JSON payload in bounded pieces, not as a decoded execution fact."""
        if (type(after_sequence) is not int or not 0 <= after_sequence <= 2**63 - 1
                or type(content_offset) is not int or not 0 <= content_offset <= MAX_STORED_PAYLOAD_BYTES
                or type(max_characters) is not int or not 1 <= max_characters <= 16000):
            raise InvalidInput("History fragment cursor or bound is invalid")
        run = await self._row(tenant_id, run_id)
        if after_sequence > run.latest_history_sequence:
            raise InvalidInput("History fragment cursor is beyond this Run")
        body = sql_cast(RunHistoryRecord.payload, Text)
        record = (await self._session.execute(select(RunHistoryRecord.sequence, RunHistoryRecord.payload_kind,
            RunHistoryRecord.payload_schema_version, func.char_length(body).label("characters"),
            func.octet_length(body).label("bytes")).where(RunHistoryRecord.tenant_id == tenant_id,
                RunHistoryRecord.run_id == run_id, RunHistoryRecord.sequence > after_sequence,
                RunHistoryRecord.sequence <= run.latest_history_sequence).order_by(RunHistoryRecord.sequence).limit(1))).one_or_none()
        if record is None:
            if after_sequence < run.latest_history_sequence:
                raise InvalidHistory("History sequences are not contiguous")
            if content_offset:
                raise InvalidInput("History fragment offset has no target entry")
            return None
        if record.sequence != after_sequence + 1:
            raise InvalidHistory("History sequences are not contiguous")
        if record.payload_kind not in get_args(HistoryKind) or not supported_history_version(record.payload_kind, record.payload_schema_version):
            raise InvalidHistory("History fragment has an unsupported kind or version")
        if record.bytes > MAX_STORED_PAYLOAD_BYTES:
            raise InvalidHistory("History entry exceeds its byte limit")
        if content_offset >= record.characters:
            raise InvalidInput("History fragment offset is outside the entry")
        fragment = await self._session.scalar(select(func.substring(body, content_offset + 1, max_characters)).where(
            RunHistoryRecord.tenant_id == tenant_id, RunHistoryRecord.run_id == run_id,
            RunHistoryRecord.sequence == record.sequence, RunHistoryRecord.payload_kind == record.payload_kind,
            RunHistoryRecord.payload_schema_version == record.payload_schema_version,
            func.char_length(body) == record.characters, func.octet_length(body) == record.bytes))
        if fragment is None:
            raise InvalidHistory("History changed during its bounded fragment read")
        next_offset = content_offset + len(fragment)
        finished = next_offset == record.characters
        return HistoryFragment(record.sequence, cast(HistoryKind, record.payload_kind), record.payload_schema_version,
            fragment, None if finished else next_offset,
            record.sequence if finished else after_sequence, run.latest_history_sequence)

    async def start(self, *, tenant_id: UUID, agent_id: UUID, run_id: UUID, source: SourceIdentity,
            input: InputContent, snapshot: RunSnapshot, parent_run_id: UUID | None = None,
            admit: Callable[[], None] | None = None) -> StartResult:
        """Call the synchronous admission port only after deduplication; the caller owns its reservation and commit."""
        if (snapshot.tenant_id, snapshot.agent_id, snapshot.workspace.run_id, snapshot.role) != (
                tenant_id, agent_id, run_id, "sub" if parent_run_id else "main"):
            raise InvalidInput("Snapshot does not match its Run identity and role")
        parent = None
        if parent_run_id is not None:
            parent = await self._row(tenant_id, parent_run_id, lock=True)
            if parent.parent_run_id is not None or parent.agent_id != agent_id:
                raise InvalidInput("Only a Main Run can create a Child for the same Agent")
            if source.owner_id != parent_run_id or source.kind != "task":
                raise InvalidInput("Child source must identify its Parent Task Tool call")
        existing = await self.find_by_source(tenant_id=tenant_id, source=source)
        if existing is not None:
            if existing.agent_id != agent_id or existing.parent_run_id != parent_run_id:
                raise Conflict("Run source already belongs to another execution scope")
            return StartResult(existing, False)
        if parent is not None:
            if parent.status not in _ACTIVE:
                raise Conflict("Terminal Parent cannot create a Child")
            inherited = derive_child(await self.read_snapshot(tenant_id=tenant_id, run_id=parent.id), run_id=run_id)
            if snapshot != inherited:
                raise InvalidInput("Child Snapshot must inherit its Parent authorization")
        if admit is not None:
            admit()
        now = datetime.now(UTC)
        inserted = await self._session.scalar(insert(RunRecord).values(id=run_id, tenant_id=tenant_id,
            agent_id=agent_id, parent_run_id=parent_run_id, status="Running", initiator_kind=source.kind,
            initiator_owner_id=source.owner_id, source_key=source.key, latest_history_sequence=0 if parent is not None else 1,
            created_at=now, started_at=now, updated_at=now).on_conflict_do_nothing(
                constraint="uq_agent_runs_source_identity").returning(RunRecord))
        if inserted is None:
            existing = await self.find_by_source(tenant_id=tenant_id, source=source)
            if existing is None or existing.agent_id != agent_id or existing.parent_run_id != parent_run_id:
                raise Conflict("Run source already belongs to another execution scope")
            return StartResult(existing, False)
        if parent is None:
            await self._initialize_main(inserted, snapshot, input, source)
            return StartResult(_view(inserted), True)
        await self._snapshots.insert(run_id=run_id, snapshot=snapshot)
        await self._history.append(tenant_id=tenant_id, run_id=run_id, payload=InitialInputPayload(input), source=source)
        return StartResult(await self.get(tenant_id=tenant_id, run_id=run_id), True)

    async def _initialize_main(self, row: RunRecord, snapshot: RunSnapshot, input: InputContent, source: SourceIdentity) -> None:
        """Only this owner's INSERT winner enters here; no existing Run can be initialized again."""
        encoded_snapshot, _ = _prepare_snapshot(snapshot)
        encoded_input = encode_history(InitialInputPayload(input))
        self._session.add(RunSnapshotRecord(run_id=row.id, tenant_id=row.tenant_id, payload_kind=SNAPSHOT_KIND,
            schema_version=encoded_snapshot.version, payload=encoded_snapshot.payload,
            content_hash=encoded_snapshot.content_hash, created_at=row.created_at))
        self._session.add(RunHistoryRecord(run_id=row.id, tenant_id=row.tenant_id, sequence=1,
            payload_kind=encoded_input.kind, payload_schema_version=encoded_input.version, payload=encoded_input.payload,
            source_kind=source.kind, source_owner_id=source.owner_id, source_key=source.key, created_at=row.created_at))
        await self._session.flush()

    async def append_related(self, *, tenant_id: UUID, run_id: UUID, input: InputContent,
            source: SourceIdentity, waiting_reference: str | None = None) -> TransitionResult:
        row, parent = await self._family_lock(tenant_id, run_id)
        if parent is not None and (source.kind != "parent_answer" or source.owner_id != parent.id):
            raise InvalidInput("Child input must be supplied by its Parent Run")
        previous = (await self._session.execute(select(RunHistoryRecord.sequence, RunHistoryRecord.payload_kind).where(
            RunHistoryRecord.tenant_id == tenant_id, RunHistoryRecord.run_id == run_id,
            RunHistoryRecord.source_kind == source.kind, RunHistoryRecord.source_owner_id == source.owner_id,
            RunHistoryRecord.source_key == source.key))).one_or_none()
        if previous is not None:
            if previous.payload_kind not in ("initial_input", "related_input"):
                raise Conflict("Input source collides with an execution fact")
            await self._history.read_page(tenant_id=tenant_id, run_id=run_id,
                after_sequence=previous.sequence - 1, through_sequence=previous.sequence, limit=1)
            return TransitionResult(_view(row), False)
        self._active(row)
        if waiting_reference is not None and row.active_waiting_reference != waiting_reference:
            raise Conflict("Waiting reference is no longer active")
        result = await self._history.append(tenant_id=tenant_id, run_id=run_id,
            payload=RelatedInputPayload(input), source=source)
        if result.appended:
            row.status = "Running"
            row.active_waiting_reference = None
            await self._session.flush()
        return TransitionResult(_view(row), result.appended, wake_run_ids=(run_id,) if result.appended else (),
            affected=(_view(row),) if result.appended else ())

    async def record_history(self, *, tenant_id: UUID, run_id: UUID,
            payload: HistoryPayload, source: SourceIdentity) -> AppendHistoryResult:
        """Context observations only; execution and lifecycle facts use their dedicated methods."""
        if isinstance(payload, (InitialInputPayload, RelatedInputPayload, ModelStepPayload,
                ToolResultPayload, WaitingPayload, TerminalOutcomePayload)):
            raise InvalidInput("This History fact requires its lifecycle operation")
        row, _ = await self._family_lock(tenant_id, run_id)
        self._active(row)
        return await self._history.append(tenant_id=tenant_id, run_id=run_id, payload=payload, source=source)

    async def record_model_step(self, *, tenant_id: UUID, run_id: UUID,
            payload: ModelStepPayload) -> AppendHistoryResult:
        row, _ = await self._family_lock(tenant_id, run_id)
        self._active(row)
        if row.status != "Running" or not 1 <= payload.read_through_sequence <= row.latest_history_sequence:
            raise InvalidInput("Model Step has an invalid Run read boundary")
        return await self._history.append(tenant_id=tenant_id, run_id=run_id, payload=payload,
            source=SourceIdentity("model_step", run_id, payload.step_id))

    async def record_tool_result(self, *, tenant_id: UUID, run_id: UUID,
            payload: ToolResultPayload) -> AppendHistoryResult:
        row, _ = await self._family_lock(tenant_id, run_id)
        self._active(row)
        if row.status != "Running":
            raise Conflict("Waiting Run must resume before executing Tools")
        step = await self._step(row, payload.step_id)
        if not any(call.call_id == payload.result.call_id and call.name == payload.tool_name for call in step.result.calls):
            raise InvalidInput("Tool Result has no matching Model Tool call")
        return await self._history.append(tenant_id=tenant_id, run_id=run_id, payload=payload,
            source=SourceIdentity("tool_result", run_id,
                sha256(f"{payload.step_id}\0{payload.result.call_id}".encode()).hexdigest()))

    async def wait(self, *, tenant_id: UUID, run_id: UUID, payload: WaitingPayload) -> TransitionResult:
        row, parent = await self._family_lock(tenant_id, run_id)
        self._active(row)
        if row.status == "Waiting" and row.active_waiting_reference != payload.reference:
            raise Conflict("Waiting Run must resume before replacing its wait")
        step = await self._step(row, payload.step_id)
        if payload.read_through_sequence != step.read_through_sequence:
            raise InvalidInput("Waiting boundary does not match its Model Step")
        if await self._unseen(row, step):
            return TransitionResult(_view(row), False, wake_run_ids=(run_id,))
        if not payload.question:
            if parent is not None:
                raise InvalidInput("Subagents cannot wait for delegated Tasks")
            active_child = await self._session.scalar(select(RunRecord.id).where(
                RunRecord.tenant_id == tenant_id, RunRecord.parent_run_id == run_id,
                RunRecord.status.in_(_ACTIVE)).limit(1))
            if active_child is None:
                return TransitionResult(_view(row), False, wake_run_ids=(run_id,))
        appended = await self._history.append(tenant_id=tenant_id, run_id=run_id, payload=payload,
            source=SourceIdentity("waiting", run_id, payload.reference))
        if not appended.appended:
            return TransitionResult(_view(row), False)
        row.status, row.active_waiting_reference = "Waiting", payload.reference
        await self._session.flush()
        wake = ()
        if parent is not None:
            await self._notify(parent, row, appended.entry.sequence, payload.question, "needs_input")
            wake = (parent.id,)
        return TransitionResult(_view(row), True, wake_run_ids=wake,
            affected=(_view(row), _view(parent)) if parent else (_view(row),))

    async def complete(self, *, tenant_id: UUID, run_id: UUID, step_id: str, output: str,
            consumer: OutcomeConsumer | None = None) -> TransitionResult:
        row, parent = await self._family_lock(tenant_id, run_id)
        if row.status not in _ACTIVE:
            return TransitionResult(_view(row), False)
        if row.status != "Running":
            raise Conflict("Waiting Run must resume before completing")
        step = await self._step(row, step_id)
        if await self._unseen(row, step):
            return TransitionResult(_view(row), False, wake_run_ids=(run_id,))
        return await self._settle(row, parent, TerminalOutcomePayload("Completed", output), consumer)

    async def terminate(self, *, tenant_id: UUID, run_id: UUID,
            status: Literal["Failed", "Cancelled", "Interrupted"], reason: str,
            consumer: OutcomeConsumer | None = None) -> TransitionResult:
        if status not in ("Failed", "Cancelled", "Interrupted"):
            raise InvalidInput("Invalid termination status")
        row, parent = await self._family_lock(tenant_id, run_id)
        if row.status not in _ACTIVE:
            return TransitionResult(_view(row), False)
        return await self._settle(row, parent, TerminalOutcomePayload(status, reason=reason), consumer)

    async def interrupt_batch(self, *, limit: int = 100) -> tuple[RunView, ...]:
        """Stopped-intake maintenance only; caller commits one bounded family batch."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Interruption batch limit is invalid")
        child_rows = aliased(RunRecord)
        unfinished_child = select(child_rows.id).where(child_rows.tenant_id == RunRecord.tenant_id,
            child_rows.parent_run_id == RunRecord.id, child_rows.status.in_(_ACTIVE)).exists()
        roots = (await self._session.scalars(select(RunRecord).where(RunRecord.parent_run_id.is_(None),
            or_(RunRecord.status.in_(_ACTIVE), unfinished_child)).order_by(RunRecord.id).limit(limit).with_for_update())).all()
        ended: list[RunView] = []
        for root in roots:
            if root.status in _ACTIVE:
                if len(ended) >= MAX_TRANSACTION_RUNS:
                    raise Conflict("Interruption transaction exceeds its affected Run bound; use a smaller family batch")
                await self._terminal(root, TerminalOutcomePayload("Interrupted", reason="service_interruption"), None)
                ended.append(_view(root))
            async for child in await self._session.stream_scalars(select(RunRecord).where(
                    RunRecord.tenant_id == root.tenant_id, RunRecord.parent_run_id == root.id,
                    RunRecord.status.in_(_ACTIVE)).order_by(RunRecord.id).with_for_update().execution_options(yield_per=100)):
                if len(ended) >= MAX_TRANSACTION_RUNS:
                    raise Conflict("Interruption transaction exceeds its affected Run bound; use a smaller family batch")
                await self._terminal(child, TerminalOutcomePayload("Interrupted", reason="service_interruption"), None)
                ended.append(_view(child))
        return tuple(ended)

    async def _settle(self, row: RunRecord, parent: RunRecord | None, outcome: TerminalOutcomePayload,
            consumer: OutcomeConsumer | None) -> TransitionResult:
        await self._terminal(row, outcome, consumer)
        ended = [row.id]
        affected = [_view(row)]
        if parent is None:
            async for child in await self._session.stream_scalars(select(RunRecord).where(
                    RunRecord.tenant_id == row.tenant_id, RunRecord.parent_run_id == row.id,
                    RunRecord.status.in_(_ACTIVE)).order_by(RunRecord.id).with_for_update().execution_options(yield_per=100)):
                if len(ended) >= MAX_TRANSACTION_RUNS:
                    raise Conflict("Run family exceeds its atomic settlement bound")
                await self._terminal(child, TerminalOutcomePayload("Cancelled", reason="parent_terminated"), None)
                ended.append(child.id)
                affected.append(_view(child))
        elif parent.status in _ACTIVE:
            await self._notify(parent, row, row.latest_history_sequence, outcome.output or outcome.reason or "", outcome.status)
            affected.append(_view(parent))
        return TransitionResult(_view(row), True, tuple(ended), (parent.id,) if parent and parent.status in _ACTIVE else (),
            tuple(affected))

    async def _terminal(self, row: RunRecord, payload: TerminalOutcomePayload,
            consumer: OutcomeConsumer | None) -> None:
        row.status, row.active_waiting_reference, row.finished_at = payload.status, None, datetime.now(UTC)
        await self._history.append(tenant_id=row.tenant_id, run_id=row.id, payload=payload)
        if consumer is not None:
            await consumer.record_outcome(self._transaction, run=_view(row), outcome=payload)

    async def _notify(self, parent: RunRecord, child: RunRecord, sequence: int, text: str, kind: str) -> None:
        self._active(parent)
        # The full result stays in the Child History; the Parent receives a bounded preview and explicit reference.
        preview = text.encode()[:8192].decode(errors="ignore")
        if preview != text:
            preview += " [preview truncated; read referenced Child History]"
        await self._history.append(tenant_id=parent.tenant_id, run_id=parent.id,
            payload=RelatedInputPayload(InputContent(f"Child {child.id} {kind}: {preview}",
                (InputReference(f"run:{child.id}:history:{sequence}"),))),
            source=SourceIdentity("child_result", child.id, str(sequence)))
        parent.status, parent.active_waiting_reference = "Running", None
        await self._session.flush()

    async def _step(self, row: RunRecord, step_id: str) -> ModelStepPayload:
        sequence = await self._session.scalar(select(RunHistoryRecord.sequence).where(RunHistoryRecord.tenant_id == row.tenant_id,
            RunHistoryRecord.run_id == row.id, RunHistoryRecord.source_kind == "model_step",
            RunHistoryRecord.source_owner_id == row.id, RunHistoryRecord.source_key == step_id))
        if sequence is None:
            raise InvalidInput("Decision requires a committed successful Model Step")
        latest_step = await self._session.scalar(select(RunHistoryRecord.sequence).where(
            RunHistoryRecord.tenant_id == row.tenant_id, RunHistoryRecord.run_id == row.id,
            RunHistoryRecord.payload_kind == "model_step").order_by(RunHistoryRecord.sequence.desc()).limit(1))
        if sequence != latest_step:
            raise Conflict("Decision must use the latest successful Model Step")
        page = await self._history.read_page(tenant_id=row.tenant_id, run_id=row.id,
            after_sequence=sequence - 1, through_sequence=sequence, limit=1)
        payload = page.entries[0].payload
        if not isinstance(payload, ModelStepPayload):
            raise Conflict("Model Step source contains an invalid History kind")
        return payload

    async def _unseen(self, row: RunRecord, step: ModelStepPayload) -> bool:
        return await self._history.has_unseen_related_input(tenant_id=row.tenant_id, run_id=row.id,
            after_read_boundary=step.read_through_sequence)

    async def _row(self, tenant_id: UUID, run_id: UUID, *, lock: bool = False) -> RunRecord:
        query = select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id)
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Run does not exist in this Tenant")
        return row

    async def _family_lock(self, tenant_id: UUID, run_id: UUID) -> tuple[RunRecord, RunRecord | None]:
        probe = await self._row(tenant_id, run_id)
        parent = await self._row(tenant_id, probe.parent_run_id, lock=True) if probe.parent_run_id else None
        row = await self._row(tenant_id, run_id, lock=True)
        return row, parent

    @staticmethod
    def _active(row: RunRecord) -> None:
        if row.status not in _ACTIVE:
            raise Conflict("Terminal Run cannot resume or accept execution input")
