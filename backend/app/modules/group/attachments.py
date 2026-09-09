"""Group-owned input attachments; no Session persistence or Workspace import."""

import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as content_digest
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.group.models import (
    GroupAttachmentRecord,
    GroupEventRecord,
    GroupMembershipRecord,
    GroupRecord,
    GroupRunLinkRecord,
)
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import RunService, RunView

MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024


class GroupAttachmentObject(Protocol):
    @property
    def revision(self) -> str: ...
    @property
    def byte_size(self) -> int: ...
    @property
    def sha256(self) -> str: ...


class GroupAttachmentStorage(Protocol):
    """One resource guard covers publication/cleanup; DB transactions remain short."""
    def guard(self, storage_key: str) -> AbstractAsyncContextManager[None]: ...
    async def put_if_absent(self, storage_key: str, content: bytes) -> GroupAttachmentObject: ...
    async def inspect(self, storage_key: str) -> GroupAttachmentObject | None: ...
    async def read_range(self, storage_key: str, *, revision: str, offset: int, limit: int) -> bytes: ...
    async def delete_if_revision(self, storage_key: str, *, revision: str) -> bool: ...


class GroupAttachmentDelegation(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView, reference: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GroupAttachmentView:
    id: UUID
    tenant_id: UUID
    group_id: UUID
    uploader_membership_id: UUID
    filename: str
    media_type: str
    byte_size: int
    sha256: str
    origin_event_id: UUID | None
    published_at: datetime | None
    unbound_expires_at: datetime
    cleanup_claimed_at: datetime | None

    @property
    def reference(self) -> str:
        return f"attachment:group:{self.id}"


@dataclass(frozen=True, slots=True)
class GroupAttachmentBlob:
    """Private physical coordinates consumed by the application storage adapter."""
    view: GroupAttachmentView
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


def _blob(row: GroupAttachmentRecord) -> GroupAttachmentBlob:
    _metadata(row.filename, row.media_type, row.byte_size, row.sha256)
    key = f"input-attachments/group/{row.tenant_id}/{row.group_id}/{row.id}"
    if (row.storage_key != key or (row.published_at is None) != (row.storage_revision is None)
            or (row.cleanup_claimed_at is not None and row.origin_event_id is not None)):
        raise InvalidInput("Group attachment storage metadata is invalid")
    if row.storage_revision is not None and (not row.storage_revision or len(row.storage_revision) > 512):
        raise InvalidInput("Attachment storage revision is invalid")
    return GroupAttachmentBlob(GroupAttachmentView(row.id, row.tenant_id, row.group_id, row.uploader_membership_id,
        row.filename, row.media_type, row.byte_size, row.sha256, row.origin_event_id, row.published_at, row.unbound_expires_at, row.cleanup_claimed_at),
        row.storage_key, row.storage_revision)


class GroupAttachmentService:
    def __init__(self, transaction: TransactionContext, *, delegated_access: GroupAttachmentDelegation | None = None) -> None:
        self.tx, self.session, self.delegated_access = transaction, transaction.session, delegated_access

    async def _message_scope(self, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str) -> tuple[UUID, UUID, UUID]:
        run = await RunService(self.tx).verify_main_tool_origin(tenant_id=tenant_id, run_id=run_id,
            step_id=step_id, call_id=call_id, tool_name="send_message")
        if run.source.kind != "group":
            raise AccessDenied("Run has no Group message destination")
        linked = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == tenant_id,
            GroupRunLinkRecord.event_id == run.source.owner_id, GroupRunLinkRecord.run_id == run_id,
            GroupRunLinkRecord.agent_id == run.agent_id))
        if linked is None:
            raise AccessDenied("Run has no matching Group message destination")
        await self.session.scalar(select(GroupRecord).where(GroupRecord.tenant_id == tenant_id,
            GroupRecord.id == linked.group_id).with_for_update())
        event = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.id == linked.event_id, GroupEventRecord.kind == "input"))
        if event is None or event.membership_id is None:
            raise AccessDenied("Group execution has no source Membership")
        return linked.group_id, event.membership_id, event.id

    @staticmethod
    def _message_source(run_id: UUID, step_id: str, call_id: str, ordinal: int) -> str:
        if type(ordinal) is not int or not 0 <= ordinal < 8:
            raise InvalidInput("Message attachment ordinal is invalid")
        return "message:" + content_digest(f"{run_id}\0{step_id}\0{call_id}\0{ordinal}".encode()).hexdigest()

    async def begin_run_upload(self, *, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str, ordinal: int,
            filename: str, media_type: str, byte_size: int, sha256: str) -> GroupAttachmentBlob:
        owner_id, membership_id, _ = await self._message_scope(tenant_id, run_id, step_id, call_id)
        return await self._begin(tenant_id=tenant_id, group_id=owner_id, membership_id=membership_id,
            upload_source_key=self._message_source(run_id, step_id, call_id, ordinal),
            filename=filename, media_type=media_type, byte_size=byte_size, sha256=sha256)

    async def publish_run_upload(self, *, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str, ordinal: int,
            attachment_id: UUID, revision: str, byte_size: int, sha256: str) -> GroupAttachmentView:
        owner_id, membership_id, _ = await self._message_scope(tenant_id, run_id, step_id, call_id)
        row = await self._row(tenant_id, attachment_id, lock=True)
        if (row.group_id, row.uploader_membership_id, row.upload_source_key) != (
                owner_id, membership_id, self._message_source(run_id, step_id, call_id, ordinal)):
            raise AccessDenied("Attachment is not this message's prepared upload")
        return await self._publish(row, revision=revision, byte_size=byte_size, sha256=sha256)

    async def get_run_upload(self, *, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str, ordinal: int,
            attachment_id: UUID) -> GroupAttachmentBlob:
        owner_id, membership_id, _ = await self._message_scope(tenant_id, run_id, step_id, call_id)
        row = await self._row(tenant_id, attachment_id)
        if (row.group_id, row.uploader_membership_id, row.upload_source_key) != (
                owner_id, membership_id, self._message_source(run_id, step_id, call_id, ordinal)):
            raise AccessDenied("Attachment is not this message's prepared upload")
        self._available(row, _time(None), published=False)
        return _blob(row)

    async def bind_message_uploads(self, *, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str,
            message_id: UUID, attachment_ids: tuple[UUID, ...]) -> None:
        owner_id, membership_id, source_input = await self._message_scope(tenant_id, run_id, step_id, call_id)
        if len(attachment_ids) > 8 or len(set(attachment_ids)) != len(attachment_ids):
            raise InvalidInput("Message attachment count is invalid")
        message = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.id == message_id, GroupEventRecord.source_run_id == run_id, GroupEventRecord.kind == "reply"))
        if message is None:
            raise AccessDenied("Attachment binding requires this Run's committed message")
        payload = message.payload.get("input", {})
        references = {item["reference"] for item in payload.get("references", [])}
        for ordinal, identity in enumerate(attachment_ids):
            row = await self._row(tenant_id, identity, lock=True)
            self._available(row, _time(None))
            if (row.group_id, row.uploader_membership_id, row.upload_source_key) != (
                    owner_id, membership_id, self._message_source(run_id, step_id, call_id, ordinal)) or _blob(row).view.reference not in references:
                raise AccessDenied("Message attachment does not match its prepared source")
            if row.origin_event_id is None:
                row.origin_event_id, row.updated_at = source_input, _time(None)
        await self.session.flush()

    async def authorize_delivery(self, *, tenant_id: UUID, agent_id: UUID, message_id: UUID,
            attachment_id: UUID) -> GroupAttachmentBlob:
        message = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.id == message_id, GroupEventRecord.agent_id == agent_id, GroupEventRecord.kind == "reply"))
        if message is None or message.source_run_id is None:
            raise AccessDenied("Attachment delivery requires an accepted Agent message")
        row = await self._row(tenant_id, attachment_id)
        self._available(row, _time(None))
        payload = message.payload.get("input", {})
        if row.group_id != message.group_id or row.origin_event_id is None or not any(
                item.get("reference") == _blob(row).view.reference for item in payload.get("references", [])):
            raise AccessDenied("Message does not explicitly include this immutable attachment")
        return _blob(row)

    async def begin_upload(self, principal: TenantPrincipal, *, group_id: UUID, upload_source_key: str,
            filename: str, media_type: str, byte_size: int, sha256: str, now: datetime | None = None) -> GroupAttachmentBlob:
        if upload_source_key.startswith("message:"):
            raise InvalidInput("Message upload source keys are reserved for Run publication")
        await self._human(principal, group_id, lock=True)
        return await self._begin(tenant_id=principal.tenant_id, group_id=group_id, membership_id=principal.membership_id,
            upload_source_key=upload_source_key, filename=filename, media_type=media_type, byte_size=byte_size, sha256=sha256, now=now)

    async def _begin(self, *, tenant_id: UUID, group_id: UUID, membership_id: UUID, upload_source_key: str,
            filename: str, media_type: str, byte_size: int, sha256: str, now: datetime | None = None) -> GroupAttachmentBlob:
        _metadata(filename, media_type, byte_size, sha256)
        stamp = _time(now)
        if not upload_source_key or len(upload_source_key) > 512:
            raise InvalidInput("Attachment upload source is invalid")
        row = await self.session.scalar(select(GroupAttachmentRecord).where(GroupAttachmentRecord.tenant_id == tenant_id,
            GroupAttachmentRecord.group_id == group_id, GroupAttachmentRecord.upload_source_key == upload_source_key))
        if row is not None:
            if row.uploader_membership_id != membership_id:
                raise AccessDenied("Upload source belongs to another Group member")
            if (row.filename, row.media_type, row.byte_size, row.sha256) != (filename, media_type, byte_size, sha256):
                raise Conflict("Upload source already identifies different content")
            self._available(row, stamp, published=False)
            return _blob(row)
        identity = uuid4()
        row = GroupAttachmentRecord(id=identity, tenant_id=tenant_id, group_id=group_id,
            uploader_membership_id=membership_id, upload_source_key=upload_source_key, filename=filename,
            media_type=media_type, byte_size=byte_size, sha256=sha256, origin_event_id=None,
            storage_key=f"input-attachments/group/{tenant_id}/{group_id}/{identity}", storage_revision=None,
            published_at=None, unbound_expires_at=stamp + timedelta(hours=24), cleanup_claimed_at=None, created_at=stamp, updated_at=stamp)
        self.session.add(row)
        await self.session.flush()
        return _blob(row)

    async def get_upload(self, principal: TenantPrincipal, *, group_id: UUID, attachment_id: UUID,
            now: datetime | None = None) -> GroupAttachmentBlob:
        """Recheck the pending record under the application's per-object storage guard."""
        await self._human(principal, group_id)
        row = await self._row(principal.tenant_id, attachment_id)
        if row.group_id != group_id or row.uploader_membership_id != principal.membership_id:
            raise AccessDenied("Upload does not belong to this Group member")
        self._available(row, _time(now), published=False)
        return _blob(row)

    async def publish_upload(self, principal: TenantPrincipal, *, group_id: UUID, attachment_id: UUID,
            revision: str, byte_size: int, sha256: str, now: datetime | None = None) -> GroupAttachmentView:
        await self._human(principal, group_id)
        row = await self._row(principal.tenant_id, attachment_id, lock=True)
        if row.group_id != group_id or row.uploader_membership_id != principal.membership_id:
            raise AccessDenied("Upload does not belong to this Group member")
        return await self._publish(row, revision=revision, byte_size=byte_size, sha256=sha256, now=now)

    async def _publish(self, row: GroupAttachmentRecord, *, revision: str, byte_size: int, sha256: str,
            now: datetime | None = None) -> GroupAttachmentView:
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

    async def bind_to_input(self, principal: TenantPrincipal, *, group_id: UUID, event_id: UUID,
            attachment_ids: tuple[UUID, ...], now: datetime | None = None) -> tuple[GroupAttachmentView, ...]:
        await self._human(principal, group_id, lock=True)
        if len(attachment_ids) > 64 or len(set(attachment_ids)) != len(attachment_ids):
            raise InvalidInput("Attachment binding count is invalid")
        event = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == principal.tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.id == event_id, GroupEventRecord.kind == "input"))
        if event is None or event.payload_version != 1 or event.membership_id != principal.membership_id or not isinstance(event.payload, dict):
            raise AccessDenied("Attachment binding requires this member's Group input")
        payload = event.payload.get("input")
        references = payload.get("references") if isinstance(payload, dict) else None
        if not isinstance(references, (list, tuple)) or len(references) > 64 or any(
                not isinstance(item, dict) or not isinstance(item.get("reference"), str) for item in references):
            raise InvalidInput("Group input references are invalid")
        names = {item.get("reference") for item in references if isinstance(item, dict) and isinstance(item.get("reference"), str)}
        rows = (await self.session.scalars(select(GroupAttachmentRecord).where(GroupAttachmentRecord.tenant_id == principal.tenant_id,
            GroupAttachmentRecord.group_id == group_id, GroupAttachmentRecord.id.in_(attachment_ids))
            .order_by(GroupAttachmentRecord.id).with_for_update())).all()
        if len(rows) != len(attachment_ids):
            raise AccessDenied("One or more attachments belong to another Group")
        stamp = _time(now)
        for row in rows:
            self._available(row, stamp)
            if _blob(row).view.reference not in names:
                raise InvalidInput("Attachment is not explicitly referenced by this input")
            if row.origin_event_id is None and row.uploader_membership_id != principal.membership_id:
                raise AccessDenied("Another member's unsubmitted upload is private")
        for row in rows:
            if row.origin_event_id is None:
                row.origin_event_id, row.updated_at = event_id, stamp
        await self.session.flush()
        return tuple(_blob(row).view for row in rows)

    async def authorize_read(self, principal: TenantPrincipal, *, group_id: UUID, attachment_id: UUID,
            now: datetime | None = None) -> GroupAttachmentBlob:
        await self._human(principal, group_id)
        row = await self._row(principal.tenant_id, attachment_id)
        if row.group_id != group_id or (row.origin_event_id is None and row.uploader_membership_id != principal.membership_id):
            raise AccessDenied("Group attachment access is denied")
        self._available(row, _time(now))
        return _blob(row)

    async def authorize_run_read(self, *, tenant_id: UUID, run_id: UUID, attachment_id: UUID) -> GroupAttachmentBlob:
        row = await self._row(tenant_id, attachment_id)
        self._available(row, _time(None))
        if row.origin_event_id is None:
            raise AccessDenied("Execution cannot read an unsubmitted upload")
        runs = RunService(self.tx)
        run = await runs.get(tenant_id=tenant_id, run_id=run_id)
        if run.parent_run_id is not None:
            run = await runs.get(tenant_id=tenant_id, run_id=run.parent_run_id)
        reference = _blob(row).view.reference
        if run.source.kind in ("trigger", "heartbeat"):
            snapshot = await runs.read_snapshot(tenant_id=tenant_id, run_id=run.id)
            if (snapshot.workspace.output.kind != "group" or snapshot.workspace.output.id != row.group_id
                    or not await runs.has_input_reference(tenant_id=tenant_id, run_id=run.id, reference=reference)):
                raise AccessDenied("Scheduled execution lacks this explicit Group attachment scope")
            return _blob(row)
        if run.source.kind == "a2a":
            if self.delegated_access is None:
                raise AccessDenied("A2A attachment access requires an explicit delegation")
            await self.delegated_access(self.tx, run=run, reference=reference)
            return _blob(row)
        link = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == tenant_id,
            GroupRunLinkRecord.run_id == run.id, GroupRunLinkRecord.group_id == row.group_id,
            GroupRunLinkRecord.agent_id == run.agent_id))
        if run.source.kind != "group" or link is None or run.source.owner_id != link.event_id:
            raise AccessDenied("Execution does not belong to this attachment's Group")
        cutoff = await self.session.scalar(select(GroupEventRecord.position).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.group_id == row.group_id, GroupEventRecord.id == link.event_id))
        if row.upload_source_key.startswith("message:"):
            published = (await self.session.execute(select(GroupEventRecord.position, GroupEventRecord.source_run_id).where(
                GroupEventRecord.tenant_id == tenant_id, GroupEventRecord.group_id == row.group_id,
                GroupEventRecord.origin_event_id == row.origin_event_id,
                GroupEventRecord.kind == "reply", GroupEventRecord.payload["input"]["references"].contains(
                    [{"reference": reference}])).order_by(GroupEventRecord.position).limit(1))).one_or_none()
            if published is None:
                raise AccessDenied("Generated attachment has no accepted message")
            if published.source_run_id == run.id:
                return _blob(row)
            position = published.position
        else:
            position = await self.session.scalar(select(GroupEventRecord.position).where(GroupEventRecord.tenant_id == tenant_id,
                GroupEventRecord.group_id == row.group_id, GroupEventRecord.id == row.origin_event_id))
        if cutoff is None or position is None or (position > cutoff and not await runs.has_input_reference(
                tenant_id=tenant_id, run_id=run.id, reference=reference)):
            raise AccessDenied("Attachment is outside the Run's fixed input cutoff")
        return _blob(row)

    async def expired_unbound(self, *, now: datetime, after_id: UUID | None = None,
            limit: int = 100) -> tuple[GroupAttachmentBlob, ...]:
        stamp = _time(now)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Attachment cleanup page is invalid")
        query = select(GroupAttachmentRecord).where(GroupAttachmentRecord.origin_event_id.is_(None),
            or_(GroupAttachmentRecord.unbound_expires_at <= stamp, GroupAttachmentRecord.cleanup_claimed_at.is_not(None)))
        if after_id is not None:
            query = query.where(GroupAttachmentRecord.id > after_id)
        return tuple(_blob(row) for row in (await self.session.scalars(query.order_by(GroupAttachmentRecord.id).limit(limit))).all())

    async def claim_cleanup(self, observed: GroupAttachmentBlob, *, now: datetime) -> GroupAttachmentBlob | None:
        """Commit before physical deletion; claimed files cannot subsequently bind to an event."""
        query = select(GroupAttachmentRecord).where(GroupAttachmentRecord.id == observed.view.id,
            GroupAttachmentRecord.tenant_id == observed.view.tenant_id, GroupAttachmentRecord.group_id == observed.view.group_id)
        row = await self.session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if row is None or row.origin_event_id is not None:
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

    async def finish_cleanup(self, observed: GroupAttachmentBlob, *, now: datetime) -> bool:
        """Physical deletion must finish before the matching committed cleanup claim is removed."""
        _time(now)
        if observed.view.cleanup_claimed_at is None:
            return False
        row = GroupAttachmentRecord
        removed = await self.session.scalar(delete(row).where(row.id == observed.view.id, row.tenant_id == observed.view.tenant_id,
            row.group_id == observed.view.group_id, row.origin_event_id.is_(None), row.cleanup_claimed_at == observed.view.cleanup_claimed_at,
            row.published_at == observed.view.published_at, row.storage_revision == observed.storage_revision,
            row.storage_key == observed.storage_key, row.sha256 == observed.view.sha256).returning(row.id))
        return removed is not None

    async def _human(self, principal: TenantPrincipal, group_id: UUID, *, lock: bool = False) -> GroupRecord:
        query = select(GroupRecord).where(GroupRecord.tenant_id == principal.tenant_id, GroupRecord.id == group_id)
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFound("Group does not exist")
        member = await self.session.scalar(select(GroupMembershipRecord.id).where(GroupMembershipRecord.tenant_id == principal.tenant_id,
            GroupMembershipRecord.group_id == group_id, GroupMembershipRecord.membership_id == principal.membership_id,
            GroupMembershipRecord.enabled.is_(True)))
        if not row.enabled or member is None:
            raise AccessDenied("Active Group membership is required for attachments")
        return row

    async def _row(self, tenant_id: UUID, attachment_id: UUID, *, lock: bool = False) -> GroupAttachmentRecord:
        query = select(GroupAttachmentRecord).where(GroupAttachmentRecord.tenant_id == tenant_id, GroupAttachmentRecord.id == attachment_id)
        row = await self.session.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Group attachment does not exist")
        _blob(row)
        return row

    @staticmethod
    def _available(row: GroupAttachmentRecord, now: datetime, *, published: bool = True) -> None:
        if row.cleanup_claimed_at is not None:
            raise Conflict("Attachment removal has been claimed")
        if row.origin_event_id is None and row.unbound_expires_at <= now:
            raise Conflict("Unsubmitted attachment has expired")
        if published and row.published_at is None:
            raise Conflict("Attachment bytes are not published")
