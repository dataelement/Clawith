"""Private Run History persistence; caller owns transactions and lifecycle decisions."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.run.contracts import (
    MAX_NODES,
    MAX_RECORD_BYTES,
    HistoryPayload,
    InitialInputPayload,
    InvalidHistory,
    RelatedInputPayload,
    decode_history,
    encode_history,
)
from app.modules.run.models import RunHistoryRecord, RunRecord

MAX_PAGE_ENTRIES = 100
MAX_PAGE_BYTES = 32 * 1024 * 1024
# JSONB textual output inserts spaces absent from the codec's compact representation.
MAX_STORED_PAYLOAD_BYTES = MAX_RECORD_BYTES + MAX_NODES


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Existing owner-issued correlation, also usable for retrying committed execution facts."""
    kind: str
    owner_id: UUID
    key: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, UUID) or not isinstance(self.kind, str) or not isinstance(self.key, str):
            raise InvalidInput("History source identity is invalid")
        try:
            invalid = not self.kind.strip() or len(self.kind.encode()) > 64 or not self.key or len(self.key.encode()) > 512
        except UnicodeError:
            raise InvalidInput("History source identity is invalid") from None
        if invalid:
            raise InvalidInput("History source identity is invalid")


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    tenant_id: UUID
    run_id: UUID
    sequence: int
    payload: HistoryPayload
    source: SourceIdentity | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AppendHistoryResult:
    entry: HistoryEntry
    appended: bool


@dataclass(frozen=True, slots=True)
class HistoryPage:
    entries: tuple[HistoryEntry, ...]
    through_sequence: int
    next_after_sequence: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class _Metadata:
    sequence: int
    kind: str
    version: int
    source_kind: str | None
    source_owner_id: UUID | None
    source_key: str | None
    created_at: datetime
    payload_bytes: int


_PAYLOAD_BYTES = func.octet_length(cast(RunHistoryRecord.payload, Text))


def _metadata_query(tenant_id: UUID, run_id: UUID):
    return select(RunHistoryRecord.sequence, RunHistoryRecord.payload_kind, RunHistoryRecord.payload_schema_version,
        RunHistoryRecord.source_kind, RunHistoryRecord.source_owner_id, RunHistoryRecord.source_key,
        RunHistoryRecord.created_at, _PAYLOAD_BYTES).where(
            RunHistoryRecord.tenant_id == tenant_id, RunHistoryRecord.run_id == run_id)


class RunHistoryRepository:
    def __init__(self, transaction: TransactionContext) -> None:
        self._session = transaction.session

    async def append(self, *, tenant_id: UUID, run_id: UUID, payload: HistoryPayload,
            source: SourceIdentity | None = None) -> AppendHistoryResult:
        is_input = isinstance(payload, (InitialInputPayload, RelatedInputPayload))
        if is_input and source is None:
            raise InvalidInput("Input History requires a source identity")
        encoded = encode_history(payload)
        run = await self._session.scalar(select(RunRecord).where(RunRecord.tenant_id == tenant_id,
            RunRecord.id == run_id).with_for_update().execution_options(populate_existing=True))
        if run is None:
            raise NotFound("Run does not exist in this Tenant")
        if source is not None:
            found = (await self._session.execute(_metadata_query(tenant_id, run_id).where(
                RunHistoryRecord.source_kind == source.kind, RunHistoryRecord.source_owner_id == source.owner_id,
                RunHistoryRecord.source_key == source.key))).one_or_none()
            if found is not None:
                metadata = _Metadata(*found)
                if metadata.payload_bytes > MAX_STORED_PAYLOAD_BYTES:
                    raise InvalidHistory("History entry exceeds its byte limit")
                entries = await self._fetch(tenant_id, run_id, (metadata,))
                return AppendHistoryResult(entries[0], False)
        now = datetime.now(UTC)
        sequence = run.latest_history_sequence + 1
        self._session.add(RunHistoryRecord(tenant_id=tenant_id, run_id=run_id, sequence=sequence,
            payload_kind=encoded.kind, payload_schema_version=encoded.version, payload=encoded.payload,
            source_kind=source.kind if source else None, source_owner_id=source.owner_id if source else None,
            source_key=source.key if source else None, created_at=now))
        run.latest_history_sequence = sequence
        run.updated_at = now
        await self._session.flush()
        return AppendHistoryResult(HistoryEntry(tenant_id, run_id, sequence,
            decode_history(encoded.kind, encoded.version, encoded.payload), source, now), True)

    async def read_page(self, *, tenant_id: UUID, run_id: UUID, after_sequence: int = 0,
            through_sequence: int | None = None, limit: int = MAX_PAGE_ENTRIES,
            max_bytes: int = MAX_PAGE_BYTES) -> HistoryPage:
        _sequence(after_sequence)
        if through_sequence is not None:
            _sequence(through_sequence)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_ENTRIES:
            raise InvalidInput("History page count is invalid")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PAGE_BYTES:
            raise InvalidInput("History page byte limit is invalid")
        latest = await self._latest(tenant_id, run_id)
        upper = latest if through_sequence is None else through_sequence
        if upper > latest or after_sequence > upper:
            raise InvalidInput("History page boundary is invalid")
        candidates = tuple(_Metadata(*row) for row in (await self._session.execute(
            _metadata_query(tenant_id, run_id).where(RunHistoryRecord.sequence > after_sequence,
                RunHistoryRecord.sequence <= upper).order_by(RunHistoryRecord.sequence).limit(limit + 1))).all())
        if (not candidates and after_sequence < upper) or any(
                row.sequence != after_sequence + index + 1 for index, row in enumerate(candidates)):
            raise InvalidHistory("History sequences are not contiguous")
        # Reserve the page wrapper with maximum-width cursor values before fetching any JSON.
        used = len(json.dumps({"entries": [], "through_sequence": upper, "next_after_sequence": upper,
            "has_more": False}, separators=(",", ":")).encode())
        if used > max_bytes:
            raise InvalidInput("History page byte limit cannot hold its envelope")
        selected: list[_Metadata] = []
        for row in candidates[:limit]:
            if row.payload_bytes > MAX_STORED_PAYLOAD_BYTES:
                raise InvalidHistory("History entry exceeds its byte limit")
            size = _entry_bytes(tenant_id, run_id, row) + (1 if selected else 0)
            if used + size > max_bytes:
                if not selected:
                    raise InvalidInput("History entry cannot fit this page byte limit")
                break
            selected.append(row)
            used += size
        entries = await self._fetch(tenant_id, run_id, tuple(selected))
        next_after = entries[-1].sequence if entries else after_sequence
        return HistoryPage(entries, upper, next_after, next_after < upper)

    async def has_unseen_related_input(self, *, tenant_id: UUID, run_id: UUID,
            after_read_boundary: int) -> bool:
        _sequence(after_read_boundary)
        latest = await self._latest(tenant_id, run_id)
        if after_read_boundary > latest:
            raise InvalidInput("History read boundary is beyond this Run")
        return bool(await self._session.scalar(select(RunHistoryRecord.sequence).where(
            RunHistoryRecord.tenant_id == tenant_id, RunHistoryRecord.run_id == run_id,
            RunHistoryRecord.payload_kind == "related_input", RunHistoryRecord.sequence > after_read_boundary).limit(1)))

    async def _latest(self, tenant_id: UUID, run_id: UUID) -> int:
        value = await self._session.scalar(select(RunRecord.latest_history_sequence).where(
            RunRecord.tenant_id == tenant_id, RunRecord.id == run_id))
        if value is None:
            raise NotFound("Run does not exist in this Tenant")
        return value

    async def _fetch(self, tenant_id: UUID, run_id: UUID, metadata: tuple[_Metadata, ...]) -> tuple[HistoryEntry, ...]:
        if not metadata:
            return ()
        expected = {row.sequence: row for row in metadata}
        # Metadata reads use uncompressed PostgreSQL JSON text bytes, not TOAST's stored size.
        # This second bound also excludes a row enlarged between metadata and payload queries.
        conditions = [(RunHistoryRecord.sequence == row.sequence) & (_PAYLOAD_BYTES <= row.payload_bytes) for row in metadata]
        rows = (await self._session.scalars(select(RunHistoryRecord).where(
            RunHistoryRecord.tenant_id == tenant_id, RunHistoryRecord.run_id == run_id,
            or_(*conditions)).order_by(RunHistoryRecord.sequence).execution_options(populate_existing=True))).all()
        if len(rows) != len(metadata):
            raise InvalidHistory("History changed during its bounded read")
        result = []
        for row in rows:
            previous = expected[row.sequence]
            if (row.payload_kind, row.payload_schema_version, row.source_kind, row.source_owner_id,
                    row.source_key, row.created_at) != (previous.kind, previous.version, previous.source_kind,
                    previous.source_owner_id, previous.source_key, previous.created_at):
                raise InvalidHistory("History metadata changed during its read")
            source_fields = (row.source_kind, row.source_owner_id, row.source_key)
            if any(value is not None for value in source_fields) and any(value is None for value in source_fields):
                raise InvalidHistory("History source identity is incomplete")
            source = None
            if row.source_kind is not None and row.source_owner_id is not None and row.source_key is not None:
                try:
                    source = SourceIdentity(row.source_kind, row.source_owner_id, row.source_key)
                except InvalidInput:
                    raise InvalidHistory("History source identity is invalid") from None
            value = decode_history(row.payload_kind, row.payload_schema_version, row.payload)
            if isinstance(value, (InitialInputPayload, RelatedInputPayload)) and source is None:
                raise InvalidHistory("History source identity is inconsistent")
            result.append(HistoryEntry(tenant_id, run_id, row.sequence, value, source, row.created_at))
        return tuple(result)


def _entry_bytes(tenant_id: UUID, run_id: UUID, row: _Metadata) -> int:
    header = {"tenant_id": str(tenant_id), "run_id": str(run_id), "sequence": row.sequence,
        "kind": row.kind, "version": row.version, "created_at": row.created_at.isoformat(),
        "source": None if row.source_kind is None else {"kind": row.source_kind,
            "owner_id": str(row.source_owner_id), "key": row.source_key}, "payload": None}
    return len(json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()) - 4 + row.payload_bytes


def _sequence(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise InvalidInput("History sequence is invalid")
