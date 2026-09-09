"""Request-owned temporary publication, frozen returns and confirmed source saves."""

import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import load_only

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.a2a.models import A2ARequestRecord
from app.modules.run.public import RunService

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 65536


@dataclass(frozen=True, slots=True)
class TempStoredFile:
    revision: str
    byte_size: int
    sha256: str


class TempStoredObject(Protocol):
    @property
    def revision(self) -> str: ...
    @property
    def byte_size(self) -> int: ...
    @property
    def sha256(self) -> str: ...


class A2ATempStorage(Protocol):
    def guard(self, key: str) -> AbstractAsyncContextManager[None]: ...
    async def write(self, key: str, content: bytes, *, expected_revision: str | None) -> TempStoredObject: ...
    async def read(self, key: str) -> tuple[bytes, TempStoredObject]: ...
    async def inspect(self, key: str) -> TempStoredObject | None: ...
    async def delete(self, key: str, *, revision: str) -> bool: ...


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class Publication(_Value):
    operation: str = Field(min_length=1, max_length=256)
    expected_revision: str | None = Field(max_length=512)
    byte_size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=256)


class SaveReceipt(_Value):
    run_id: str
    operation: str = Field(min_length=1, max_length=256)
    subject_kind: Literal["agent", "membership", "group", "a2a"]
    subject_id: str
    path: str = Field(min_length=1, max_length=512)
    expected_revision: str | None = Field(max_length=512)
    revision: str | None = Field(default=None, max_length=512)


class TempFileView(_Value):
    name: str = Field(min_length=1, max_length=200)
    media_type: str = Field(min_length=1, max_length=256)
    byte_size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str | None = Field(default=None, max_length=512)
    pending: Publication | None = None
    returned: bool = False
    save: SaveReceipt | None = None
    cleanup_claimed: bool = False
    cleaned: bool = False
    publication_operation: str | None = Field(default=None, max_length=256)


@dataclass(frozen=True, slots=True)
class ReturnedFile:
    name: str
    media_type: str
    byte_size: int
    sha256: str
    revision: str


class _Manifest(_Value):
    files: tuple[TempFileView, ...] = Field(default=(), max_length=8)


@dataclass(frozen=True, slots=True)
class TempFilePlan:
    tenant_id: UUID
    request_id: UUID
    file: TempFileView
    storage_key: str = field(repr=False)
    resuming_save: bool = False


def _name(value: str) -> None:
    if not value or value in (".", "..") or any(c in value for c in ("/", "\\", "\0", "\r", "\n")):
        raise InvalidInput("Temporary files require a logical filename, not a filesystem path")
    try:
        if len(value.encode()) > 200:
            raise ValueError
    except (UnicodeError, ValueError):
        raise InvalidInput("Temporary filename exceeds its bound") from None


def _manifest(row: A2ARequestRecord, candidate: dict[str, object] | None = None) -> _Manifest:
    try:
        raw = json.dumps(row.temp_files_manifest if candidate is None else candidate, ensure_ascii=False, allow_nan=False)
        if row.temp_files_version != 1 or len(raw.encode()) > MAX_MANIFEST_BYTES:
            raise ValueError
        result = _Manifest.model_validate_json(raw)
        if len({file.name for file in result.files}) != len(result.files):
            raise ValueError
        for file in result.files:
            _name(file.name)
            if (file.returned and (file.revision is None or file.pending is not None)) or (file.save and not file.returned):
                raise ValueError
        if sum(max(file.byte_size, file.pending.byte_size if file.pending else 0) for file in result.files if not file.cleaned) > MAX_TOTAL_BYTES:
            raise ValueError
        return result
    except (ValueError, UnicodeError, RecursionError, ValidationError):
        raise InvalidInput("A2A temporary-file manifest is invalid") from None


def _plan(row: A2ARequestRecord, file: TempFileView) -> TempFilePlan:
    return TempFilePlan(row.tenant_id, row.id, file,
        f"a2a-temporary/{row.tenant_id}/{row.id}/{sha256(file.name.encode()).hexdigest()}")


class A2ATempFileService:
    def __init__(self, transaction: TransactionContext) -> None:
        self.tx, self.session = transaction, transaction.session

    async def _row(self, tenant_id: UUID, request_id: UUID, *, lock: bool = False) -> A2ARequestRecord:
        size = func.octet_length(cast(A2ARequestRecord.temp_files_manifest, Text))
        query = select(A2ARequestRecord).where(A2ARequestRecord.tenant_id == tenant_id, A2ARequestRecord.id == request_id,
            size <= MAX_MANIFEST_BYTES).options(load_only(A2ARequestRecord.id, A2ARequestRecord.tenant_id,
                A2ARequestRecord.source_agent_id, A2ARequestRecord.source_run_id, A2ARequestRecord.delivery_run_id,
                A2ARequestRecord.target_agent_id, A2ARequestRecord.target_run_id,
                A2ARequestRecord.temp_files_version, A2ARequestRecord.temp_files_manifest, A2ARequestRecord.updated_at))
        row = await self.session.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))
        if row is None:
            raise NotFound("A2A request or bounded temporary-file manifest is unavailable")
        return row

    async def _target(self, tenant_id: UUID, run_id: UUID, *, lock: bool = False) -> A2ARequestRecord:
        runs = RunService(self.tx)
        run = await runs.get(tenant_id=tenant_id, run_id=run_id, lock=lock)
        if run.status != "Running":
            raise AccessDenied("Only an executing target may change temporary files")
        parent = await runs.get(tenant_id=tenant_id, run_id=run.parent_run_id) if run.parent_run_id else run
        if parent.source.kind != "a2a" or parent.status not in ("Running", "Waiting"):
            raise AccessDenied("Temporary files belong to an active A2A target family")
        row = await self._row(tenant_id, parent.source.owner_id, lock=lock)
        if row.target_run_id != parent.id or row.target_agent_id != run.agent_id:
            raise AccessDenied("Run is not this A2A request's target")
        return row

    async def _source(self, tenant_id: UUID, run_id: UUID, request_id: UUID, *, lock: bool = False) -> A2ARequestRecord:
        run = await RunService(self.tx).get(tenant_id=tenant_id, run_id=run_id, lock=lock)
        row = await self._row(tenant_id, request_id, lock=lock)
        if run.parent_run_id is not None or run.agent_id != row.source_agent_id or run.id != (row.delivery_run_id or row.source_run_id):
            raise AccessDenied("Only the current A2A delivery Main may save returned files")
        return row

    async def _store(self, row: A2ARequestRecord, file: TempFileView) -> TempFilePlan:
        files = list(_manifest(row).files)
        index = next((i for i, existing in enumerate(files) if existing.name == file.name), None)
        if index is None:
            files.append(file)
        else:
            files[index] = file
        try:
            value = _Manifest(files=tuple(files)).model_dump(mode="json")
        except ValidationError:
            raise InvalidInput("A2A temporary file count exceeds eight") from None
        _manifest(row, value)
        row.temp_files_manifest = value
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return _plan(row, file)

    @staticmethod
    def _find(row: A2ARequestRecord, name: str) -> TempFileView:
        _name(name)
        found = next((file for file in _manifest(row).files if file.name == name), None)
        if found is None:
            raise NotFound("A2A temporary file does not exist")
        return found

    async def prepare_write(self, *, tenant_id: UUID, run_id: UUID, name: str, publication: Publication) -> TempFilePlan:
        _name(name)
        row = await self._target(tenant_id, run_id, lock=True)
        old = next((file for file in _manifest(row).files if file.name == name), None)
        if old is not None and (old.returned or old.cleanup_claimed or old.cleaned):
            raise Conflict("Returned or cleanup-claimed temporary files cannot change")
        if old and old.pending is not None:
            if old.pending.model_copy(update={"operation": publication.operation}) != publication:
                raise Conflict("Another temporary publication is unresolved")
            return _plan(row, old)
        if old and old.publication_operation == publication.operation and (
                old.byte_size, old.sha256, old.media_type) == (publication.byte_size, publication.sha256, publication.media_type):
            return _plan(row, old)
        if publication.expected_revision != (old.revision if old else None):
            raise Conflict("Temporary file revision changed")
        value = old or TempFileView(name=name, media_type=publication.media_type, byte_size=0,
            sha256=sha256(b"").hexdigest())
        return await self._store(row, value.model_copy(update={"pending": publication}))

    async def publish(self, *, tenant_id: UUID, run_id: UUID, name: str, publication: Publication,
            stored: TempStoredObject) -> TempFilePlan:
        row = await self._target(tenant_id, run_id, lock=True)
        file = self._find(row, name)
        if file.pending != publication or file.cleanup_claimed or file.returned or not stored.revision or (
                stored.byte_size, stored.sha256) != (publication.byte_size, publication.sha256):
            raise Conflict("Temporary publication no longer matches its recorded intent")
        return await self._store(row, file.model_copy(update={"revision": stored.revision, "byte_size": stored.byte_size,
            "sha256": stored.sha256, "media_type": publication.media_type, "pending": None,
            "publication_operation": publication.operation}))

    async def target_file(self, *, tenant_id: UUID, run_id: UUID, name: str) -> TempFilePlan:
        row = await self._target(tenant_id, run_id)
        file = self._find(row, name)
        if file.revision is None or file.pending is not None or file.cleanup_claimed or file.cleaned:
            raise Conflict("Temporary file is not available as a confirmed revision")
        return _plan(row, file)

    async def return_file(self, *, tenant_id: UUID, run_id: UUID, name: str, expected_revision: str) -> TempFilePlan:
        row = await self._target(tenant_id, run_id, lock=True)
        file = self._find(row, name)
        if file.revision != expected_revision or file.pending or file.cleaned or file.cleanup_claimed:
            raise Conflict("Return requires an available exact temporary revision")
        return await self._store(row, file.model_copy(update={"returned": True}))

    async def returned_files(self, *, tenant_id: UUID, request_id: UUID) -> tuple[TempFileView, ...]:
        row = await self._row(tenant_id, request_id)
        return tuple(file for file in _manifest(row).files if file.returned)

    async def returned_file_info(self, *, tenant_id: UUID, request_id: UUID) -> tuple[ReturnedFile, ...]:
        files = await self.returned_files(tenant_id=tenant_id, request_id=request_id)
        return tuple(ReturnedFile(file.name, file.media_type, file.byte_size, file.sha256, file.revision)
            for file in files if file.revision is not None)

    async def source_file(self, *, tenant_id: UUID, run_id: UUID, request_id: UUID, name: str) -> TempFilePlan:
        row = await self._source(tenant_id, run_id, request_id)
        file = self._find(row, name)
        if not file.returned or file.cleanup_claimed or file.cleaned:
            raise AccessDenied("Only retained returned files are readable by the source")
        return _plan(row, file)

    async def prepare_save(self, *, tenant_id: UUID, run_id: UUID, request_id: UUID, name: str, receipt: SaveReceipt) -> TempFilePlan:
        row = await self._source(tenant_id, run_id, request_id, lock=True)
        file = self._find(row, name)
        if receipt.subject_kind == "a2a":
            target_request = await self._target(tenant_id, run_id, lock=True)
            _name(receipt.path)
            if receipt.subject_id != str(target_request.id) or target_request.id == request_id:
                raise AccessDenied("A nested return must be copied into this Run's own request")
        else:
            snapshot = await RunService(self.tx).read_snapshot(tenant_id=tenant_id, run_id=run_id)
            if (receipt.subject_kind, receipt.subject_id) != (snapshot.workspace.output.kind, str(snapshot.workspace.output.id)) or not receipt.path.startswith("files/"):
                raise AccessDenied("Save intent must use this Main's captured output Workspace")
        if file.save is not None:
            destination = (receipt.subject_kind, receipt.subject_id, receipt.path)
            previous_destination = (file.save.subject_kind, file.save.subject_id, file.save.path)
            if file.save.revision is not None and destination == previous_destination:
                return replace(_plan(row, file), resuming_save=True)
            if file.save.revision is None and destination == previous_destination and file.save != receipt:
                run = await RunService(self.tx).get(tenant_id=tenant_id, run_id=run_id)
                if run.status != "Running":
                    raise AccessDenied("Only an executing source may replace an unresolved save intent")
                plan = await self._store(row, file.model_copy(update={"save": receipt}))
                return replace(plan, resuming_save=True)
            if file.save.model_copy(update={"revision": None}) != receipt:
                raise Conflict("Returned file already has another save intent")
            return replace(_plan(row, file), resuming_save=True)
        run = await RunService(self.tx).get(tenant_id=tenant_id, run_id=run_id)
        if run.status != "Running" or not file.returned or file.cleaned or file.cleanup_claimed:
            raise AccessDenied("Only an executing delivery Main can begin saving a retained return")
        if receipt.run_id != str(run_id) or receipt.revision is not None:
            raise InvalidInput("Save intent must identify its executing Run")
        return await self._store(row, file.model_copy(update={"save": receipt}))

    async def confirm_save(self, *, tenant_id: UUID, run_id: UUID, request_id: UUID, name: str,
            receipt: SaveReceipt, revision: str) -> TempFilePlan:
        row = await self._source(tenant_id, run_id, request_id, lock=True)
        file = self._find(row, name)
        if file.save != receipt or not revision:
            raise Conflict("Save confirmation differs from its recorded intent")
        if receipt.subject_kind == "a2a":
            target = await self._target(tenant_id, run_id, lock=True)
            copied = self._find(target, receipt.path)
            if str(target.id) != receipt.subject_id or copied.pending or copied.cleaned or (
                    copied.revision, copied.sha256, copied.byte_size) != (revision, file.sha256, file.byte_size):
                raise Conflict("Nested return requires a confirmed matching target temporary revision")
        return await self._store(row, file.model_copy(update={"save": receipt.model_copy(update={"revision": revision})}))

    async def reject_save(self, *, tenant_id: UUID, run_id: UUID, request_id: UUID, name: str, receipt: SaveReceipt) -> None:
        """Only a confirmed no-write conflict permits replacing the destination intent."""
        row = await self._source(tenant_id, run_id, request_id, lock=True)
        file = self._find(row, name)
        if file.save != receipt:
            raise Conflict("Save rejection differs from its recorded intent")
        await self._store(row, file.model_copy(update={"save": None}))

    async def cleanup_candidates(self, *, after_id: UUID | None = None, limit: int = 100) -> tuple[tuple[UUID, UUID], ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Temporary cleanup page is invalid")
        query = select(A2ARequestRecord.tenant_id, A2ARequestRecord.id).where(
            A2ARequestRecord.temp_files_manifest["files"].contains([{"cleaned": False}]))
        if after_id is not None:
            query = query.where(A2ARequestRecord.id > after_id)
        return tuple((tenant, identity) for tenant, identity in (await self.session.execute(query.order_by(A2ARequestRecord.id).limit(limit))).all())

    async def files(self, *, tenant_id: UUID, request_id: UUID) -> tuple[TempFilePlan, ...]:
        row = await self._row(tenant_id, request_id)
        return tuple(_plan(row, file) for file in _manifest(row).files if not file.cleaned)

    async def claim_cleanup(self, *, tenant_id: UUID, request_id: UUID, name: str) -> TempFilePlan | None:
        before = await self._row(tenant_id, request_id)
        target = await RunService(self.tx).get(tenant_id=tenant_id, run_id=before.target_run_id, lock=True) if before.target_run_id else None
        row = await self._row(tenant_id, request_id, lock=True)
        file = self._find(row, name)
        if file.cleaned or (file.returned and (file.save is None or file.save.revision is None)):
            return None
        if not file.returned and (target is None or target.status in ("Running", "Waiting")):
            return None
        return await self._store(row, file.model_copy(update={"cleanup_claimed": True}))

    async def finish_cleanup(self, observed: TempFilePlan) -> None:
        row = await self._row(observed.tenant_id, observed.request_id, lock=True)
        file = self._find(row, observed.file.name)
        if file != observed.file or not file.cleanup_claimed:
            raise Conflict("Temporary cleanup observation changed")
        await self._store(row, file.model_copy(update={"cleaned": True, "pending": None}))
