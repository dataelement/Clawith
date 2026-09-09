"""Session-owned immutable input files; physical storage is an application port."""

import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import InputContent, RunService, RunView
from app.modules.session.models import SessionAttachmentRecord, SessionEntryRecord, SessionRecord, SessionRunLinkRecord

MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024


class SessionAttachmentObject(Protocol):
    @property
    def revision(self) -> str: ...
    @property
    def byte_size(self) -> int: ...
    @property
    def sha256(self) -> str: ...


class SessionAttachmentStorage(Protocol):
    """Guard spans physical I/O and short owner transactions, never a transaction across I/O."""
    def guard(self, storage_key: str) -> AbstractAsyncContextManager[None]: ...
    async def put_if_absent(self, storage_key: str, content: bytes) -> SessionAttachmentObject: ...
    async def inspect(self, storage_key: str) -> SessionAttachmentObject | None: ...
    async def read_range(self, storage_key: str, *, revision: str, offset: int, limit: int) -> bytes: ...
    async def delete_if_revision(self, storage_key: str, *, revision: str) -> bool: ...


class SessionAttachmentDelegation(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView, reference: str) -> None: ...


class SessionRunAttachmentAuthorizer(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView,
        target_id: UUID, conversation_id: UUID | None, input: InputContent) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionAttachmentView:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    uploader_membership_id: UUID | None
    filename: str
    media_type: str
    byte_size: int
    sha256: str
    origin_input_id: UUID | None
    published_at: datetime | None
    unbound_expires_at: datetime
    cleanup_claimed_at: datetime | None
    created_by_run_id: UUID | None = None
    bound_message_id: UUID | None = None

    @property
    def reference(self) -> str:
        return f"attachment:session:{self.id}"


@dataclass(frozen=True, slots=True)
class SessionAttachmentBlob:
    """Internal I/O coordinates; transports expose only the safe view."""
    view: SessionAttachmentView
    storage_key: str = field(repr=False)
    storage_revision: str | None = field(repr=False)


def _time(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise InvalidInput("Attachment time requires a timezone")
    return result.astimezone(UTC)


def _metadata(filename: str, media_type: str, byte_size: int, sha256: str) -> None:
    try:
        filename_size = len(filename.encode("utf-8"))
        media_size = len(media_type.encode("ascii"))
    except UnicodeError:
        raise InvalidInput("Attachment filename or media type encoding is invalid") from None
    if not filename or filename_size > 512 or any(c in filename for c in ("\0", "\r", "\n")):
        raise InvalidInput("Attachment filename is invalid")
    if media_size > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*", media_type):
        raise InvalidInput("Attachment media type is invalid")
    if type(byte_size) is not int or not 0 <= byte_size <= MAX_ATTACHMENT_BYTES or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise InvalidInput("Attachment size or content digest is invalid")


def _blob(row: SessionAttachmentRecord) -> SessionAttachmentBlob:
    _metadata(row.filename, row.media_type, row.byte_size, row.sha256)
    key = f"input-attachments/session/{row.tenant_id}/{row.session_id}/{row.id}"
    if (row.storage_key != key or (row.published_at is None) != (row.storage_revision is None)
            or (row.cleanup_claimed_at is not None and (row.origin_input_id is not None or row.bound_message_id is not None))
            or (row.uploader_membership_id is None) == (row.created_by_run_id is None)
            or (row.created_by_run_id is not None and row.origin_input_id is not None)):
        raise InvalidInput("Session attachment storage metadata is invalid")
    if row.storage_revision is not None and (not row.storage_revision or len(row.storage_revision) > 512):
        raise InvalidInput("Attachment storage revision is invalid")
    return SessionAttachmentBlob(SessionAttachmentView(row.id, row.tenant_id, row.session_id, row.uploader_membership_id,
        row.filename, row.media_type, row.byte_size, row.sha256, row.origin_input_id, row.published_at, row.unbound_expires_at, row.cleanup_claimed_at,
        row.created_by_run_id, row.bound_message_id),
        row.storage_key, row.storage_revision)


class SessionAttachmentService:
    def __init__(self, transaction: TransactionContext, *, delegated_access: SessionAttachmentDelegation | None = None) -> None:
        self.tx, self.session, self.delegated_access = transaction, transaction.session, delegated_access

    async def _run_destination(self, run: RunView, session_id: UUID,
            authorize: SessionRunAttachmentAuthorizer | None) -> RunView:
        actual = await RunService(self.tx).lock_main(tenant_id=run.tenant_id, run_id=run.id)
        destination = await self.session.scalar(select(SessionRecord).where(SessionRecord.tenant_id == actual.tenant_id,
            SessionRecord.id == session_id).with_for_update())
        if destination is None or destination.agent_id != actual.agent_id:
            raise AccessDenied("Run attachment destination does not match its Agent")
        if actual.source.kind == "session":
            link = await self.session.scalar(select(SessionRunLinkRecord).where(SessionRunLinkRecord.tenant_id == actual.tenant_id,
                SessionRunLinkRecord.session_id == session_id, SessionRunLinkRecord.run_id == actual.id,
                SessionRunLinkRecord.agent_id == actual.agent_id))
            if actual.source.owner_id != session_id or link is None or str(link.id) != actual.source.key:
                raise AccessDenied("Run attachment does not belong to this Session")
        elif actual.source.kind in ("trigger", "heartbeat"):
            if authorize is None:
                raise AccessDenied("External Run attachment requires frozen destination authorization")
            await authorize(self.tx, run=actual, target_id=session_id, conversation_id=None, input=InputContent(""))
        else:
            raise AccessDenied("Run has no Session attachment publication destination")
        return actual

    async def begin_run_upload(self, *, run: RunView, session_id: UUID, step_id: str, call_id: str,
            upload_source_key: str, filename: str, media_type: str, byte_size: int, sha256: str,
            authorize: SessionRunAttachmentAuthorizer | None = None, now: datetime | None = None) -> SessionAttachmentBlob:
        _metadata(filename, media_type, byte_size, sha256)
        if not upload_source_key.startswith("message:") or len(upload_source_key) > 512:
            raise InvalidInput("Attachment upload source is invalid")
        actual = await self._run_destination(run, session_id, authorize)
        row = await self.session.scalar(select(SessionAttachmentRecord).where(SessionAttachmentRecord.tenant_id == actual.tenant_id,
            SessionAttachmentRecord.session_id == session_id, SessionAttachmentRecord.upload_source_key == upload_source_key))
        stamp = _time(now)
        if row is not None:
            if row.created_by_run_id != actual.id or (row.filename, row.media_type, row.byte_size, row.sha256) != (filename, media_type, byte_size, sha256):
                raise Conflict("Run upload source already identifies another file")
            self._available(row, stamp, published=False)
            return _blob(row)
        await RunService(self.tx).verify_main_tool_origin(tenant_id=actual.tenant_id, run_id=actual.id,
            step_id=step_id, call_id=call_id, tool_name="send_message")
        identity = uuid4()
        row = SessionAttachmentRecord(id=identity, tenant_id=actual.tenant_id, session_id=session_id,
            uploader_membership_id=None, created_by_run_id=actual.id, bound_message_id=None,
            upload_source_key=upload_source_key, filename=filename, media_type=media_type, byte_size=byte_size,
            sha256=sha256, origin_input_id=None, storage_key=f"input-attachments/session/{actual.tenant_id}/{session_id}/{identity}",
            storage_revision=None, published_at=None, unbound_expires_at=stamp + timedelta(hours=24), cleanup_claimed_at=None,
            created_at=stamp, updated_at=stamp)
        self.session.add(row)
        await self.session.flush()
        return _blob(row)

    async def get_run_upload(self, *, run: RunView, session_id: UUID, attachment_id: UUID,
            authorize: SessionRunAttachmentAuthorizer | None = None, now: datetime | None = None) -> SessionAttachmentBlob:
        actual = await self._run_destination(run, session_id, authorize)
        row = await self._row(actual.tenant_id, attachment_id)
        if row.session_id != session_id or row.created_by_run_id != actual.id:
            raise AccessDenied("Attachment was not created by this Run for this Session")
        self._available(row, _time(now), published=False)
        return _blob(row)

    async def publish_run_upload(self, *, run: RunView, session_id: UUID, attachment_id: UUID,
            revision: str, byte_size: int, sha256: str, authorize: SessionRunAttachmentAuthorizer | None = None,
            now: datetime | None = None) -> SessionAttachmentView:
        actual = await self._run_destination(run, session_id, authorize)
        row = await self._row(actual.tenant_id, attachment_id, lock=True)
        if row.session_id != session_id or row.created_by_run_id != actual.id:
            raise AccessDenied("Attachment was not created by this Run for this Session")
        stamp = _time(now)
        self._available(row, stamp, published=False)
        if not revision or len(revision) > 512 or (row.byte_size, row.sha256) != (byte_size, sha256):
            raise Conflict("Run upload does not match its reserved content")
        if row.published_at is not None:
            if row.storage_revision != revision:
                raise Conflict("Attachment publication is immutable")
            return _blob(row).view
        if actual.status != "Running":
            raise Conflict("Only a running producer can publish a new attachment")
        row.storage_revision, row.published_at, row.updated_at = revision, stamp, stamp
        await self.session.flush()
        return _blob(row).view

    async def bind_to_message(self, *, run: RunView, session_id: UUID, message_id: UUID,
            attachment_ids: tuple[UUID, ...], now: datetime | None = None) -> tuple[SessionAttachmentView, ...]:
        actual = await RunService(self.tx).lock_main(tenant_id=run.tenant_id, run_id=run.id)
        if len(attachment_ids) > 8 or len(set(attachment_ids)) != len(attachment_ids):
            raise InvalidInput("Attachment binding count is invalid")
        message = await self.session.scalar(select(SessionEntryRecord).where(SessionEntryRecord.tenant_id == actual.tenant_id,
            SessionEntryRecord.session_id == session_id, SessionEntryRecord.id == message_id,
            SessionEntryRecord.kind == "reply", SessionEntryRecord.source_run_id == actual.id))
        if message is None or message.payload_version != 1 or not isinstance(message.payload, dict):
            raise AccessDenied("Run attachment binding requires its accepted message")
        references = message.payload.get("references")
        if not isinstance(references, (list, tuple)) or len(references) > 64 or any(
                not isinstance(item, dict) or not isinstance(item.get("reference"), str) for item in references):
            raise InvalidInput("Accepted message references are invalid")
        names = {item.get("reference") for item in references if isinstance(item, dict)}
        rows = tuple(await self.session.scalars(select(SessionAttachmentRecord).where(
            SessionAttachmentRecord.tenant_id == actual.tenant_id, SessionAttachmentRecord.session_id == session_id,
            SessionAttachmentRecord.id.in_(attachment_ids)).order_by(SessionAttachmentRecord.id).with_for_update()))
        if len(rows) != len(attachment_ids):
            raise AccessDenied("Run attachments belong to another destination")
        if sum(row.byte_size for row in rows) > 16 * 1024 * 1024:
            raise InvalidInput("Message attachment aggregate size exceeds its bound")
        stamp = _time(now)
        for row in rows:
            self._available(row, stamp)
            if row.created_by_run_id != actual.id or row.origin_input_id is not None or _blob(row).view.reference not in names:
                raise AccessDenied("Run attachment source or explicit message reference differs")
            if row.bound_message_id not in (None, message_id):
                raise Conflict("Run attachment is already bound to another message")
        for row in rows:
            row.bound_message_id, row.updated_at = message_id, stamp
        await self.session.flush()
        return tuple(_blob(row).view for row in rows)

    async def authorize_delivery(self, *, tenant_id: UUID, agent_id: UUID, message_id: UUID,
            attachment_id: UUID) -> SessionAttachmentBlob:
        message = await self.session.scalar(select(SessionEntryRecord).where(SessionEntryRecord.tenant_id == tenant_id,
            SessionEntryRecord.id == message_id, SessionEntryRecord.agent_id == agent_id, SessionEntryRecord.kind == "reply"))
        if message is None or message.source_run_id is None:
            raise AccessDenied("Attachment delivery requires an accepted Agent message")
        row = await self._row(tenant_id, attachment_id)
        self._available(row, _time(None))
        payload = message.payload
        if row.session_id != message.session_id or (row.origin_input_id is None and row.bound_message_id is None) or not any(
                item.get("reference") == _blob(row).view.reference for item in payload.get("references", [])):
            raise AccessDenied("Message does not explicitly include this immutable attachment")
        return await self.authorize_run_read(tenant_id=tenant_id, run_id=message.source_run_id, attachment_id=attachment_id)

    async def begin_upload(self, principal: TenantPrincipal, *, session_id: UUID, upload_source_key: str,
            filename: str, media_type: str, byte_size: int, sha256: str, now: datetime | None = None) -> SessionAttachmentBlob:
        if upload_source_key.startswith("message:"):
            raise InvalidInput("Message upload source keys are reserved for Run publication")
        await self._human(principal, session_id, lock=True)
        return await self._begin(tenant_id=principal.tenant_id, session_id=session_id, membership_id=principal.membership_id,
            upload_source_key=upload_source_key, filename=filename, media_type=media_type, byte_size=byte_size, sha256=sha256, now=now)

    async def _begin(self, *, tenant_id: UUID, session_id: UUID, membership_id: UUID, upload_source_key: str,
            filename: str, media_type: str, byte_size: int, sha256: str, now: datetime | None = None) -> SessionAttachmentBlob:
        _metadata(filename, media_type, byte_size, sha256)
        stamp = _time(now)
        if not upload_source_key or len(upload_source_key) > 512:
            raise InvalidInput("Attachment upload source is invalid")
        row = await self.session.scalar(select(SessionAttachmentRecord).where(SessionAttachmentRecord.tenant_id == tenant_id,
            SessionAttachmentRecord.session_id == session_id, SessionAttachmentRecord.upload_source_key == upload_source_key))
        if row is not None:
            if row.uploader_membership_id != membership_id or row.created_by_run_id is not None:
                raise AccessDenied("Upload source belongs to a Run rather than this human uploader")
            if (row.filename, row.media_type, row.byte_size, row.sha256) != (filename, media_type, byte_size, sha256):
                raise Conflict("Upload source already identifies different content")
            self._available(row, stamp, published=False)
            return _blob(row)
        identity = uuid4()
        row = SessionAttachmentRecord(id=identity, tenant_id=tenant_id, session_id=session_id,
            uploader_membership_id=membership_id, upload_source_key=upload_source_key, filename=filename,
            media_type=media_type, byte_size=byte_size, sha256=sha256, origin_input_id=None,
            storage_key=f"input-attachments/session/{tenant_id}/{session_id}/{identity}", storage_revision=None,
            published_at=None, unbound_expires_at=stamp + timedelta(hours=24), cleanup_claimed_at=None, created_at=stamp, updated_at=stamp)
        self.session.add(row)
        await self.session.flush()
        return _blob(row)

    async def get_upload(self, principal: TenantPrincipal, *, session_id: UUID, attachment_id: UUID,
            now: datetime | None = None) -> SessionAttachmentBlob:
        """Recheck after the application acquires the per-object storage guard."""
        await self._human(principal, session_id)
        row = await self._row(principal.tenant_id, attachment_id)
        if row.session_id != session_id or row.uploader_membership_id != principal.membership_id:
            raise AccessDenied("Attachment belongs to another Session")
        self._available(row, _time(now), published=False)
        return _blob(row)

    async def publish_upload(self, principal: TenantPrincipal, *, session_id: UUID, attachment_id: UUID,
            revision: str, byte_size: int, sha256: str, now: datetime | None = None) -> SessionAttachmentView:
        await self._human(principal, session_id)
        row = await self._row(principal.tenant_id, attachment_id, lock=True)
        if row.session_id != session_id or row.uploader_membership_id != principal.membership_id:
            raise AccessDenied("Attachment belongs to another Session")
        return await self._publish(row, revision=revision, byte_size=byte_size, sha256=sha256, now=now)

    async def _publish(self, row: SessionAttachmentRecord, *, revision: str, byte_size: int, sha256: str,
            now: datetime | None = None) -> SessionAttachmentView:
        stamp = _time(now)
        self._available(row, stamp, published=False)
        if not revision or len(revision) > 512 or (row.byte_size, row.sha256) != (byte_size, sha256):
            raise Conflict("Uploaded storage content does not match its registered metadata")
        if row.published_at is not None:
            if row.storage_revision != revision:
                raise Conflict("Attachment content is immutable after publication")
            return _blob(row).view
        row.storage_revision, row.published_at, row.updated_at = revision, stamp, stamp
        await self.session.flush()
        return _blob(row).view

    async def bind_to_input(self, principal: TenantPrincipal, *, session_id: UUID, input_id: UUID,
            attachment_ids: tuple[UUID, ...], now: datetime | None = None) -> tuple[SessionAttachmentView, ...]:
        await self._human(principal, session_id, lock=True)
        if len(attachment_ids) > 64 or len(set(attachment_ids)) != len(attachment_ids):
            raise InvalidInput("Attachment binding count is invalid")
        entry = await self.session.scalar(select(SessionEntryRecord).where(SessionEntryRecord.tenant_id == principal.tenant_id,
            SessionEntryRecord.session_id == session_id, SessionEntryRecord.id == input_id, SessionEntryRecord.kind == "input"))
        if entry is None or entry.payload_version != 1 or not isinstance(entry.payload, dict):
            raise InvalidInput("Attachment binding requires a supported Session input")
        references = entry.payload.get("references")
        if not isinstance(references, (list, tuple)) or len(references) > 64 or any(
                not isinstance(item, dict) or not isinstance(item.get("reference"), str) for item in references):
            raise InvalidInput("Session input references are invalid")
        names = {item.get("reference") for item in references if isinstance(item, dict) and isinstance(item.get("reference"), str)}
        rows = (await self.session.scalars(select(SessionAttachmentRecord).where(SessionAttachmentRecord.tenant_id == principal.tenant_id,
            SessionAttachmentRecord.session_id == session_id, SessionAttachmentRecord.id.in_(attachment_ids))
            .order_by(SessionAttachmentRecord.id).with_for_update())).all()
        if len(rows) != len(attachment_ids):
            raise AccessDenied("One or more attachments belong to another Session")
        stamp = _time(now)
        for row in rows:
            self._available(row, stamp)
            if row.created_by_run_id is not None and row.bound_message_id is None:
                raise AccessDenied("Unsent Run attachments cannot be claimed by a human input")
            if _blob(row).view.reference not in names:
                raise InvalidInput("Attachment is not explicitly referenced by this input")
        for row in rows:
            if row.origin_input_id is None and row.bound_message_id is None:
                row.origin_input_id, row.updated_at = input_id, stamp
        await self.session.flush()
        return tuple(_blob(row).view for row in rows)

    async def authorize_read(self, principal: TenantPrincipal, *, session_id: UUID, attachment_id: UUID,
            now: datetime | None = None) -> SessionAttachmentBlob:
        await self._human(principal, session_id)
        row = await self._row(principal.tenant_id, attachment_id)
        if row.session_id != session_id:
            raise AccessDenied("Attachment belongs to another Session")
        if row.created_by_run_id is not None and row.bound_message_id is None:
            raise AccessDenied("Run attachment has not been accepted as a message")
        self._available(row, _time(now))
        return _blob(row)

    async def authorize_run_read(self, *, tenant_id: UUID, run_id: UUID, attachment_id: UUID) -> SessionAttachmentBlob:
        row = await self._row(tenant_id, attachment_id)
        self._available(row, _time(None))
        runs = RunService(self.tx)
        run = await runs.get(tenant_id=tenant_id, run_id=run_id)
        if run.parent_run_id is not None:
            run = await runs.get(tenant_id=tenant_id, run_id=run.parent_run_id)
        if row.origin_input_id is None and row.bound_message_id is None:
            raise AccessDenied("Execution cannot read an unsubmitted upload")
        if row.created_by_run_id == run.id:
            return _blob(row)
        reference = _blob(row).view.reference
        if run.source.kind in ("trigger", "heartbeat"):
            snapshot = await runs.read_snapshot(tenant_id=tenant_id, run_id=run.id)
            member_id = await self.session.scalar(select(SessionRecord.membership_id).where(
                SessionRecord.tenant_id == tenant_id, SessionRecord.id == row.session_id))
            if (snapshot.workspace.output.kind != "membership" or snapshot.workspace.output.id != member_id
                    or (row.uploader_membership_id != member_id and row.bound_message_id is None) or not await runs.has_input_reference(
                        tenant_id=tenant_id, run_id=run.id, reference=reference)):
                raise AccessDenied("Scheduled execution lacks this explicit personal attachment scope")
            return _blob(row)
        if run.source.kind == "a2a":
            if self.delegated_access is None:
                raise AccessDenied("A2A attachment access requires an explicit delegation")
            await self.delegated_access(self.tx, run=run, reference=reference)
            return _blob(row)
        link = await self.session.scalar(select(SessionRunLinkRecord).where(SessionRunLinkRecord.tenant_id == tenant_id,
            SessionRunLinkRecord.run_id == run.id, SessionRunLinkRecord.session_id == row.session_id,
            SessionRunLinkRecord.agent_id == run.agent_id))
        if run.source.kind != "session" or run.source.owner_id != row.session_id or link is None or str(link.id) != run.source.key:
            raise AccessDenied("Execution does not belong to this attachment's Session")
        position = await self.session.scalar(select(SessionEntryRecord.position).where(SessionEntryRecord.tenant_id == tenant_id,
            SessionEntryRecord.session_id == row.session_id, SessionEntryRecord.id == (row.bound_message_id or row.origin_input_id)))
        if position is None or (position > link.history_cutoff and not await runs.has_input_reference(
                tenant_id=tenant_id, run_id=run.id, reference=reference)):
            raise AccessDenied("Attachment is outside the Run's fixed input cutoff")
        return _blob(row)

    async def expired_unbound(self, *, now: datetime, after_id: UUID | None = None,
            limit: int = 100) -> tuple[SessionAttachmentBlob, ...]:
        stamp = _time(now)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Attachment cleanup page is invalid")
        query = select(SessionAttachmentRecord).where(SessionAttachmentRecord.origin_input_id.is_(None), SessionAttachmentRecord.bound_message_id.is_(None),
            or_(SessionAttachmentRecord.unbound_expires_at <= stamp, SessionAttachmentRecord.cleanup_claimed_at.is_not(None)))
        if after_id is not None:
            query = query.where(SessionAttachmentRecord.id > after_id)
        return tuple(_blob(row) for row in (await self.session.scalars(query.order_by(SessionAttachmentRecord.id).limit(limit))).all())

    async def claim_cleanup(self, observed: SessionAttachmentBlob, *, now: datetime) -> SessionAttachmentBlob | None:
        """Commit this claim before deleting bytes; binding serializes on the same attachment row."""
        query = select(SessionAttachmentRecord).where(SessionAttachmentRecord.id == observed.view.id,
            SessionAttachmentRecord.tenant_id == observed.view.tenant_id, SessionAttachmentRecord.session_id == observed.view.session_id)
        row = await self.session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if row is None or row.origin_input_id is not None or row.bound_message_id is not None:
            return None
        current = _blob(row)
        if (current.storage_key, current.storage_revision, current.view.published_at, current.view.sha256) != (
                observed.storage_key, observed.storage_revision, observed.view.published_at, observed.view.sha256):
            return None
        stamp = _time(now)
        if row.cleanup_claimed_at is None:
            if row.unbound_expires_at > stamp:
                return None
            row.cleanup_claimed_at, row.updated_at = stamp, stamp
            await self.session.flush()
        return _blob(row)

    async def finish_cleanup(self, observed: SessionAttachmentBlob, *, now: datetime) -> bool:
        """Finish an existing claim only after conditional physical removal under the storage guard."""
        _time(now)
        if observed.view.cleanup_claimed_at is None:
            return False
        row = SessionAttachmentRecord
        removed = await self.session.scalar(delete(row).where(row.id == observed.view.id, row.tenant_id == observed.view.tenant_id,
            row.session_id == observed.view.session_id, row.origin_input_id.is_(None), row.bound_message_id.is_(None), row.cleanup_claimed_at == observed.view.cleanup_claimed_at,
            row.published_at == observed.view.published_at, row.storage_revision == observed.storage_revision,
            row.storage_key == observed.storage_key, row.sha256 == observed.view.sha256).returning(row.id))
        return removed is not None

    async def _human(self, principal: TenantPrincipal, session_id: UUID, *, lock: bool = False) -> SessionRecord:
        query = select(SessionRecord).where(SessionRecord.tenant_id == principal.tenant_id, SessionRecord.id == session_id)
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFound("Session does not exist")
        if row.membership_id != principal.membership_id or (not principal.can_manage_all_agents and row.agent_id not in principal.allowed_agent_ids):
            raise AccessDenied("Session attachment access is denied")
        return row

    async def _row(self, tenant_id: UUID, attachment_id: UUID, *, lock: bool = False) -> SessionAttachmentRecord:
        query = select(SessionAttachmentRecord).where(SessionAttachmentRecord.tenant_id == tenant_id, SessionAttachmentRecord.id == attachment_id)
        row = await self.session.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Session attachment does not exist")
        _blob(row)
        return row

    @staticmethod
    def _available(row: SessionAttachmentRecord, now: datetime, *, published: bool = True) -> None:
        if row.cleanup_claimed_at is not None:
            raise Conflict("Attachment removal has been claimed")
        if row.origin_input_id is None and row.bound_message_id is None and row.unbound_expires_at <= now:
            raise Conflict("Unsubmitted attachment has expired")
        if published and row.published_at is None:
            raise Conflict("Attachment bytes are not published")
