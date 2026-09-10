"""Compose product attachment owners with immutable storage and the actual Tool reader."""

import asyncio
import hashlib
import logging
import mimetypes
from collections.abc import AsyncIterable, Coroutine
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.attachment_tools import AttachmentBlob
from app.execution_dependencies.message_tools import WorkspaceMessageFile
from app.execution_dependencies.resources import ExecutionResources
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, Conflict, DomainError, InvalidInput
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.a2a.public import A2AService
from app.modules.group.public import (
    AcceptedGroupInput,
    GroupAttachmentBlob,
    GroupAttachmentService,
    GroupAttachmentView,
    GroupService,
)
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import InputContent, InputReference, RunService, RunView
from app.modules.session.public import (
    AcceptedInput,
    SessionAttachmentBlob,
    SessionAttachmentService,
    SessionAttachmentStorage,
    SessionAttachmentView,
    SessionService,
)
from app.modules.tool.public import CallScope
from app.modules.workspace.public import WorkspaceSubject

MAX_BYTES = 4 * 1024 * 1024
OwnerKind = Literal["session", "group"]
Blob = SessionAttachmentBlob | GroupAttachmentBlob
View = SessionAttachmentView | GroupAttachmentView
logger = logging.getLogger(__name__)


class MessageFileAuthorizer(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView,
            target_id: UUID, conversation_id: UUID | None, input: InputContent) -> None: ...
T = TypeVar("T")


def parse_attachment_reference(reference: str) -> tuple[OwnerKind, UUID]:
    parts = reference.split(":")
    if len(parts) != 3 or parts[0] != "attachment" or parts[1] not in ("session", "group"):
        raise InvalidInput("Use a published Session or Group attachment reference")
    try:
        identity = UUID(parts[2])
    except ValueError:
        raise InvalidInput("Attachment reference identity is invalid") from None
    if str(identity) != parts[2]:
        raise InvalidInput("Attachment reference must use its canonical identity")
    return ("session" if parts[1] == "session" else "group"), identity


def _ids(references: tuple[InputReference, ...], kind: OwnerKind) -> tuple[UUID, ...]:
    result = []
    for item in references:
        if not item.reference.startswith("attachment:"):
            continue
        namespace, identity = parse_attachment_reference(item.reference)
        if namespace != kind:
            raise AccessDenied("Input attachment belongs to another product namespace")
        if identity not in result:
            result.append(identity)
    if len(result) > 64:
        raise InvalidInput("Input attachment count exceeds its bound")
    return tuple(result)


async def _drain_write(operation: Coroutine[object, object, T]) -> T:
    """Keep the storage guard until an in-flight immutable write actually stops."""
    worker = asyncio.create_task(operation)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.wait((worker,))
            except asyncio.CancelledError:
                continue
        if not worker.cancelled():
            worker.exception()
        raise


class AttachmentInputs:
    def __init__(self, database: DatabaseResources, execution: ExecutionResources) -> None:
        if execution.input_files is None:
            raise InvalidInput("Product input storage must be configured")
        self.database = database
        self.storage: SessionAttachmentStorage = execution.input_files
        self.workspace = execution.workspace
        self._upload_admission = asyncio.Semaphore(4)
        self._io = asyncio.Semaphore(4)
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_cursors: dict[OwnerKind, UUID | None] = {"session": None, "group": None}
        self.cleanup_failures = 0

    async def read_for_delivery(self, *, tenant_id: UUID, agent_id: UUID, message_id: UUID,
            reference: str, kind: OwnerKind) -> AttachmentBlob:
        namespace, identity = parse_attachment_reference(reference)
        if namespace != kind:
            raise AccessDenied("Delivery attachment belongs to another product namespace")
        async with transaction(self.database.control_sessions) as tx:
            if kind == "session":
                await SessionService(tx).get_message_for_delivery(tenant_id=tenant_id, agent_id=agent_id, message_id=message_id)
                blob = await SessionAttachmentService(tx).authorize_delivery(tenant_id=tenant_id,
                    agent_id=agent_id, message_id=message_id, attachment_id=identity)
            else:
                await GroupService(tx).get_message_for_delivery(tenant_id=tenant_id, agent_id=agent_id, message_id=message_id)
                blob = await GroupAttachmentService(tx).authorize_delivery(tenant_id=tenant_id,
                    agent_id=agent_id, message_id=message_id, attachment_id=identity)
        return await self._read_blob(blob)

    async def prepare_message(self, *, run: RunView, step_id: str, call_id: str, input: InputContent,
            files: tuple[WorkspaceMessageFile, ...], destination: tuple[OwnerKind, UUID, UUID | None] | None = None,
            authorize: MessageFileAuthorizer | None = None) -> InputContent:
        if len(input.references) + len(files) > 8:
            raise InvalidInput("A message accepts at most eight files")
        if not input.references and not files:
            return input
        if destination is None:
            destination = await self._message_destination(run)
        kind, target, topic = destination
        async with transaction(self.database.control_sessions) as tx:
            await RunService(tx).verify_main_tool_origin(tenant_id=run.tenant_id, run_id=run.id,
                step_id=step_id, call_id=call_id, tool_name="send_message")
            snapshot = await RunService(tx).read_snapshot(tenant_id=run.tenant_id, run_id=run.id)
        references = []
        total = 0
        async with asyncio.timeout(60), self._upload_admission:
            for ordinal in range(len(input.references) + len(files)):
                if ordinal < len(input.references):
                    source = await self.read_for_run(CallScope(run.tenant_id, run.agent_id, run.id),
                        reference=input.references[ordinal].reference)
                else:
                    item = files[ordinal - len(input.references)]
                    if not item.path.startswith("files/"):
                        raise InvalidInput("Send Workspace files only from files/")
                    subject = snapshot.workspace.output if item.subject == "output" else WorkspaceSubject("agent", run.agent_id)
                    file = await self.workspace.read(snapshot.workspace, subject, item.path)
                    if file.revision != item.expected_revision:
                        raise Conflict("Workspace file changed before message capture")
                    source = AttachmentBlob(item.path.rsplit("/", 1)[-1],
                        mimetypes.guess_type(item.path)[0] or "application/octet-stream", file.content)
                total += len(source.content)
                if len(source.content) > MAX_BYTES or total > 16 * 1024 * 1024:
                    raise InvalidInput("Message files exceed their byte bound")
                digest = (await asyncio.to_thread(hashlib.sha256, source.content)).hexdigest()
                async with transaction(self.database.control_sessions) as tx:
                    source_key = "message:" + hashlib.sha256(f"{run.id}\0{step_id}\0{call_id}\0{ordinal}".encode()).hexdigest()
                    if kind == "session":
                        plan = await SessionAttachmentService(tx).begin_run_upload(run=run, session_id=target,
                            step_id=step_id, call_id=call_id, upload_source_key=source_key, filename=source.name,
                            media_type=source.media_type, byte_size=len(source.content), sha256=digest, authorize=authorize)
                    else:
                        assert topic is not None
                        plan = await GroupAttachmentService(tx).begin_run_upload(run=run, group_id=target, conversation_id=topic,
                            step_id=step_id, call_id=call_id, upload_source_key=source_key, filename=source.name,
                            media_type=source.media_type, byte_size=len(source.content), sha256=digest, authorize=authorize)
                async with self.storage.guard(plan.storage_key):
                    async with transaction(self.database.control_sessions) as tx:
                        if kind == "session":
                            await SessionAttachmentService(tx).get_run_upload(run=run, session_id=target,
                                attachment_id=plan.view.id, authorize=authorize)
                        else:
                            assert topic is not None
                            await GroupAttachmentService(tx).get_run_upload(run=run, group_id=target, conversation_id=topic,
                                attachment_id=plan.view.id, authorize=authorize)
                    async with self._io:
                        stored = await _drain_write(self.storage.put_if_absent(plan.storage_key, source.content))
                    async with transaction(self.database.control_sessions) as tx:
                        if kind == "session":
                            published = await SessionAttachmentService(tx).publish_run_upload(run=run, session_id=target,
                                attachment_id=plan.view.id, revision=stored.revision, byte_size=stored.byte_size,
                                sha256=stored.sha256, authorize=authorize)
                        else:
                            assert topic is not None
                            published = await GroupAttachmentService(tx).publish_run_upload(run=run, group_id=target,
                                conversation_id=topic, attachment_id=plan.view.id, revision=stored.revision,
                                byte_size=stored.byte_size, sha256=stored.sha256, authorize=authorize)
                references.append(InputReference(published.reference, published.filename, published.media_type))
        return InputContent(input.text, tuple(references))

    async def bind_message(self, tx: TransactionContext, *, run: RunView, step_id: str, call_id: str,
            message_id: UUID, input: InputContent, destination: tuple[OwnerKind, UUID, UUID | None] | None = None) -> None:
        if not input.references:
            return
        if destination is None:
            if run.source.kind == "session":
                destination = ("session", run.source.owner_id, None)
            elif run.source.kind == "group":
                group, topic = await GroupService(tx).execution_conversation(run)
                destination = ("group", group, topic)
            else:
                raise AccessDenied("An explicit file delivery destination is required")
        identities = tuple(parse_attachment_reference(item.reference)[1] for item in input.references)
        if destination[0] == "session":
            await SessionAttachmentService(tx).bind_to_message(run=run, session_id=destination[1],
                message_id=message_id, attachment_ids=identities)
        else:
            await GroupAttachmentService(tx).bind_to_message(run=run, group_id=destination[1],
                message_id=message_id, attachment_ids=identities)

    async def _message_destination(self, run: RunView) -> tuple[OwnerKind, UUID, UUID | None]:
        if run.source.kind == "session":
            return "session", run.source.owner_id, None
        if run.source.kind == "group":
            async with transaction(self.database.control_sessions) as tx:
                group, topic = await GroupService(tx).execution_conversation(run)
            return "group", group, topic
        raise AccessDenied("An explicit file delivery destination is required")

    async def upload_session(self, principal: TenantPrincipal, *, session_id: UUID, upload_source_key: str,
            filename: str, media_type: str, chunks: AsyncIterable[bytes]) -> SessionAttachmentView:
        view = await self._upload("session", principal, session_id, upload_source_key, filename, media_type, chunks)
        assert isinstance(view, SessionAttachmentView)
        return view

    async def upload_group(self, principal: TenantPrincipal, *, group_id: UUID, upload_source_key: str,
            filename: str, media_type: str, chunks: AsyncIterable[bytes]) -> GroupAttachmentView:
        view = await self._upload("group", principal, group_id, upload_source_key, filename, media_type, chunks)
        assert isinstance(view, GroupAttachmentView)
        return view

    async def _upload(self, kind: OwnerKind, principal: TenantPrincipal, owner_id: UUID, source: str,
            filename: str, media_type: str, chunks: AsyncIterable[bytes]) -> View:
        # Validate owner access before consuming an upload body.
        async with transaction(self.database.control_sessions) as tx:
            if kind == "session":
                await SessionService(tx).get(principal, session_id=owner_id)
            else:
                await GroupService(tx).get(principal, group_id=owner_id)
        media_type = media_type.partition(";")[0].strip().lower() or "application/octet-stream"
        try:
            async with asyncio.timeout(60), self._upload_admission:
                content = bytearray()
                async for chunk in chunks:
                    if len(content) + len(chunk) > MAX_BYTES:
                        raise InvalidInput("Input attachment exceeds four MiB")
                    content.extend(chunk)
                data = bytes(content)
                del content
                digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
                async with transaction(self.database.control_sessions) as tx:
                    if kind == "session":
                        plan = await SessionAttachmentService(tx).begin_upload(principal, session_id=owner_id,
                            upload_source_key=source, filename=filename, media_type=media_type, byte_size=len(data), sha256=digest)
                    else:
                        plan = await GroupAttachmentService(tx).begin_upload(principal, group_id=owner_id,
                            upload_source_key=source, filename=filename, media_type=media_type, byte_size=len(data), sha256=digest)
                async with self.storage.guard(plan.storage_key):
                    async with transaction(self.database.control_sessions) as tx:
                        if kind == "session":
                            current = await SessionAttachmentService(tx).get_upload(principal, session_id=owner_id, attachment_id=plan.view.id)
                        else:
                            current = await GroupAttachmentService(tx).get_upload(principal, group_id=owner_id, attachment_id=plan.view.id)
                    if current.view.published_at is not None:
                        return current.view
                    async with self._io:
                        stored = await _drain_write(self.storage.put_if_absent(current.storage_key, data))
                    async with transaction(self.database.control_sessions) as tx:
                        if kind == "session":
                            return await SessionAttachmentService(tx).publish_upload(principal, session_id=owner_id,
                                attachment_id=current.view.id, revision=stored.revision, byte_size=stored.byte_size, sha256=stored.sha256)
                        return await GroupAttachmentService(tx).publish_upload(principal, group_id=owner_id,
                            attachment_id=current.view.id, revision=stored.revision, byte_size=stored.byte_size, sha256=stored.sha256)
        except (OSError, TimeoutError):
            raise InvalidInput("Attachment upload storage is unavailable") from None

    async def bind_session(self, tx: TransactionContext, principal: TenantPrincipal, accepted: AcceptedInput) -> None:
        identities = _ids(accepted.entry.content.references, "session")
        if identities:
            await SessionAttachmentService(tx).bind_to_input(principal, session_id=accepted.entry.session_id,
                input_id=accepted.entry.id, attachment_ids=identities)

    async def bind_group(self, tx: TransactionContext, principal: TenantPrincipal, accepted: AcceptedGroupInput) -> None:
        identities = _ids(accepted.event.input.references, "group")
        if identities:
            await GroupAttachmentService(tx).bind_to_input(principal, group_id=accepted.event.group_id,
                event_id=accepted.event.id, attachment_ids=identities)

    async def authorize_source_reference(self, transaction: TransactionContext, *, run: RunView, reference: str) -> None:
        """A2A intake calls this before it records an explicit file delegation."""
        await self._authorize_run(transaction, tenant_id=run.tenant_id, run_id=run.id, reference=reference)

    async def _authorize_run(self, tx: TransactionContext, *, tenant_id: UUID, run_id: UUID, reference: str) -> Blob:
        kind, identity = parse_attachment_reference(reference)
        delegate = A2AService(tx).authorize_attachment_reference
        if kind == "session":
            return await SessionAttachmentService(tx, delegated_access=delegate).authorize_run_read(
                tenant_id=tenant_id, run_id=run_id, attachment_id=identity)
        return await GroupAttachmentService(tx, delegated_access=delegate).authorize_run_read(
            tenant_id=tenant_id, run_id=run_id, attachment_id=identity)

    async def read_for_run(self, scope: CallScope, *, reference: str) -> AttachmentBlob:
        async with transaction(self.database.control_sessions) as tx:
            run = await RunService(tx).get(tenant_id=scope.tenant_id, run_id=scope.run_id)
            if run.agent_id != scope.agent_id:
                raise AccessDenied("Attachment reader does not match the executing Agent")
            blob = await self._authorize_run(tx, tenant_id=scope.tenant_id, run_id=scope.run_id, reference=reference)
        return await self._read_blob(blob)

    async def read_document_source(self, scope: CallScope, *, reference: str) -> AttachmentBlob:
        """Read an explicit attachment or a file in the captured output Workspace."""
        if not reference.startswith("files/"):
            return await self.read_for_run(scope, reference=reference)
        async with transaction(self.database.control_sessions) as tx:
            run = await RunService(tx).get(tenant_id=scope.tenant_id, run_id=scope.run_id)
            if run.agent_id != scope.agent_id or run.status != "Running":
                raise AccessDenied("Document reader does not match the current execution")
            snapshot = await RunService(tx).read_snapshot(tenant_id=scope.tenant_id, run_id=scope.run_id)
        async with asyncio.timeout(30), self._io:
            file = await self.workspace.read(snapshot.workspace, snapshot.workspace.output, reference)
        return AttachmentBlob(reference.rsplit("/", 1)[-1],
            mimetypes.guess_type(reference)[0] or "application/octet-stream", file.content)

    async def read_human(self, principal: TenantPrincipal, *, kind: OwnerKind, owner_id: UUID,
            attachment_id: UUID) -> AttachmentBlob:
        async with transaction(self.database.control_sessions) as tx:
            if kind == "session":
                blob = await SessionAttachmentService(tx).authorize_read(principal, session_id=owner_id, attachment_id=attachment_id)
            else:
                blob = await GroupAttachmentService(tx).authorize_read(principal, group_id=owner_id, attachment_id=attachment_id)
        return await self._read_blob(blob)

    async def save_for_run(self, scope: CallScope, *, reference: str, path: str,
            expected_revision: str | None) -> str:
        """Explicitly copy authorized bytes into the Run's ordinary files output, never Memory or Skills."""
        if not path.startswith("files/"):
            raise InvalidInput("Save attachments only under the ordinary files directory")
        async with transaction(self.database.control_sessions) as tx:
            run = await RunService(tx).get(tenant_id=scope.tenant_id, run_id=scope.run_id)
            if run.agent_id != scope.agent_id or run.status != "Running":
                raise AccessDenied("Only the current executing Run can save an attachment")
        blob = await self.read_for_run(scope, reference=reference)
        async with transaction(self.database.control_sessions) as tx:
            snapshot = await RunService(tx).read_snapshot(tenant_id=scope.tenant_id, run_id=scope.run_id)
        if snapshot.agent_id != scope.agent_id:
            raise AccessDenied("Attachment save does not match the executing Agent")
        return await self.workspace.write(snapshot.workspace, snapshot.workspace.output, path, blob.content,
            expected_revision=expected_revision)

    async def _read_blob(self, blob: Blob) -> AttachmentBlob:
        if blob.storage_revision is None:
            raise Conflict("Attachment bytes are not published")
        try:
            async with asyncio.timeout(30), self._io:
                content = await self.storage.read_range(blob.storage_key, revision=blob.storage_revision,
                    offset=0, limit=MAX_BYTES)
                digest = await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())
                if len(content) != blob.view.byte_size or digest != blob.view.sha256:
                    raise InvalidInput("Attachment bytes no longer match their immutable source")
                return AttachmentBlob(blob.view.filename, blob.view.media_type, content)
        except (OSError, TimeoutError):
            raise InvalidInput("Attachment bytes are unavailable") from None

    async def cleanup_once(self, *, now: datetime | None = None, limit: int = 100) -> int:
        stamp = now or datetime.now(UTC)
        removed = 0
        for kind in ("session", "group"):
            async with transaction(self.database.control_sessions) as tx:
                if kind == "session":
                    page = await SessionAttachmentService(tx).expired_unbound(now=stamp, after_id=self._cleanup_cursors[kind], limit=limit)
                else:
                    page = await GroupAttachmentService(tx).expired_unbound(now=stamp, after_id=self._cleanup_cursors[kind], limit=limit)
            self._cleanup_cursors[kind] = page[-1].view.id if len(page) == limit else None
            for observed in page:
                try:
                    async with transaction(self.database.control_sessions) as tx:
                        if isinstance(observed, SessionAttachmentBlob):
                            claimed = await SessionAttachmentService(tx).claim_cleanup(observed, now=stamp)
                        else:
                            claimed = await GroupAttachmentService(tx).claim_cleanup(observed, now=stamp)
                    if claimed is None:
                        continue
                    async with asyncio.timeout(30), self.storage.guard(claimed.storage_key), self._io:
                        current = await self.storage.inspect(claimed.storage_key)
                        if current is not None:
                            if (current.sha256, current.byte_size) != (claimed.view.sha256, claimed.view.byte_size):
                                raise InvalidInput("Claimed attachment storage content changed")
                            if claimed.storage_revision is not None and current.revision != claimed.storage_revision:
                                raise InvalidInput("Claimed attachment storage revision changed")
                            if not await _drain_write(self.storage.delete_if_revision(claimed.storage_key, revision=current.revision)):
                                continue
                        async with transaction(self.database.control_sessions) as tx:
                            if isinstance(claimed, SessionAttachmentBlob):
                                complete = await SessionAttachmentService(tx).finish_cleanup(claimed, now=stamp)
                            else:
                                complete = await GroupAttachmentService(tx).finish_cleanup(claimed, now=stamp)
                        removed += int(complete)
                except (DomainError, OSError, TimeoutError) as error:
                    self.cleanup_failures += 1
                    logger.warning("Attachment cleanup retained a claimed resource (%s)", type(error).__name__)
        return removed

    def start_cleanup(self) -> None:
        if self._cleanup_task is not None:
            raise RuntimeError("Attachment cleanup cannot start twice")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="input-attachment-cleanup")

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup_once()
            except (DomainError, OSError, SQLAlchemyError) as error:
                self.cleanup_failures += 1
                logger.warning("Attachment cleanup scan will retry (%s)", type(error).__name__)
            await asyncio.sleep(60)

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None
