"""Temporary file I/O and source Workspace saves around short A2A transactions."""

import asyncio
import logging
from hashlib import sha256
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.attachment_inputs import _drain_write
from app.execution_dependencies.attachment_tools import AttachmentBlobReader
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, Conflict, DomainError, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import (
    A2ATempFileService,
    A2ATempStorage,
    Publication,
    SaveReceipt,
    TempFilePlan,
    TempFileView,
)
from app.modules.run.public import RunService
from app.modules.tool.public import CallScope
from app.modules.workspace.public import FileConflict, WorkspaceService

logger = logging.getLogger(__name__)


class A2ATempFiles:
    def __init__(self, database: DatabaseResources, storage: A2ATempStorage, workspace: WorkspaceService,
            attachment_reader: AttachmentBlobReader) -> None:
        self.database, self.storage, self.workspace, self.attachment_reader = database, storage, workspace, attachment_reader
        self._io = asyncio.Semaphore(4)
        self._maintenance: asyncio.Task[None] | None = None
        self.cleanup_failures = 0

    async def start_cleanup(self) -> None:
        if self._maintenance is not None:
            raise RuntimeError("Temporary cleanup already started")
        self._maintenance = asyncio.create_task(self._cleanup_loop(), name="a2a-temporary-cleanup")

    async def close(self) -> None:
        if self._maintenance is not None:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)

    async def _cleanup_loop(self) -> None:
        cursor = None
        while True:
            try:
                cursor = await self.cleanup_once(after_id=cursor)
            except (DomainError, OSError, SQLAlchemyError) as error:
                self.cleanup_failures += 1
                logger.warning("A2A temporary cleanup failed: %s", type(error).__name__)
            await asyncio.sleep(1 if cursor is not None else 60)

    async def _scope(self, scope: CallScope) -> None:
        async with transaction(self.database.control_sessions) as tx:
            run = await RunService(tx).get(tenant_id=scope.tenant_id, run_id=scope.run_id)
            if run.agent_id != scope.agent_id:
                raise AccessDenied("Temporary file scope does not match the executing Agent")

    async def write(self, scope: CallScope, *, name: str, content: bytes, media_type: str,
            expected_revision: str | None, operation: str) -> TempFileView:
        await self._scope(scope)
        if len(content) > 4 * 1024 * 1024:
            raise InvalidInput("Temporary file exceeds four MiB")
        publication = Publication(operation=operation, expected_revision=expected_revision, byte_size=len(content),
            sha256=(await asyncio.to_thread(sha256, content)).hexdigest(), media_type=media_type)
        async with transaction(self.database.control_sessions) as tx:
            plan = await A2ATempFileService(tx).prepare_write(tenant_id=scope.tenant_id, run_id=scope.run_id, name=name,
                publication=publication)
        if plan.file.pending is None:
            await self._read(plan)
            return plan.file
        # A matching retry confirms the existing intent, including its original operation identity.
        publication = plan.file.pending
        async with self.storage.guard(plan.storage_key), self._io:
            async with transaction(self.database.control_sessions) as tx:
                plan = await A2ATempFileService(tx).prepare_write(tenant_id=scope.tenant_id, run_id=scope.run_id, name=name,
                    publication=publication)
            if plan.file.pending is None:
                return plan.file
            stored = await _drain_write(self.storage.write(plan.storage_key, content, expected_revision=expected_revision))
            async with transaction(self.database.control_sessions) as tx:
                return (await A2ATempFileService(tx).publish(tenant_id=scope.tenant_id, run_id=scope.run_id,
                    name=name, publication=publication, stored=stored)).file

    async def import_attachment(self, scope: CallScope, *, reference: str, name: str,
            expected_revision: str | None, operation: str) -> TempFileView:
        value = await self.attachment_reader(scope, reference=reference)
        return await self.write(scope, name=name, content=value.content, media_type=value.media_type,
            expected_revision=expected_revision, operation=operation)

    async def import_return(self, scope: CallScope, *, request_id: UUID, source_name: str, name: str,
            expected_revision: str | None, operation: str) -> TempFileView:
        await self._scope(scope)
        async with transaction(self.database.control_sessions) as tx:
            run = await RunService(tx).get(tenant_id=scope.tenant_id, run_id=scope.run_id)
            if run.parent_run_id is not None or run.source.kind != "a2a":
                raise AccessDenied("Nested returns require a current A2A target Main")
            receipt = SaveReceipt(run_id=str(run.id), operation=operation, subject_kind="a2a",
                subject_id=str(run.source.owner_id), path=name, expected_revision=expected_revision)
            source = await A2ATempFileService(tx).prepare_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                request_id=request_id, name=source_name, receipt=receipt)
            if source.file.save and source.file.save.revision is not None:
                target = await A2ATempFileService(tx).target_file(tenant_id=scope.tenant_id, run_id=scope.run_id, name=name)
                if target.file.revision != source.file.save.revision:
                    raise Conflict("The confirmed nested copy has since changed")
                return target.file
        data = await self._read(source)
        copied = await self.write(scope, name=name, content=data, media_type=source.file.media_type,
            expected_revision=expected_revision, operation=operation)
        assert copied.revision is not None
        async with transaction(self.database.control_sessions) as tx:
            await A2ATempFileService(tx).confirm_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                request_id=request_id, name=source_name, receipt=receipt, revision=copied.revision)
        return copied

    async def read(self, scope: CallScope, *, name: str, request_id: UUID | None = None) -> tuple[bytes, TempFileView]:
        await self._scope(scope)
        async with transaction(self.database.control_sessions) as tx:
            service = A2ATempFileService(tx)
            plan = (await service.target_file(tenant_id=scope.tenant_id, run_id=scope.run_id, name=name) if request_id is None
                else await service.source_file(tenant_id=scope.tenant_id, run_id=scope.run_id, request_id=request_id, name=name))
        return await self._read(plan), plan.file

    async def _read(self, plan: TempFilePlan) -> bytes:
        async with self._io:
            data, stored = await self.storage.read(plan.storage_key)
        if (stored.revision, stored.byte_size, stored.sha256) != (plan.file.revision, plan.file.byte_size, plan.file.sha256):
            raise Conflict("Temporary bytes no longer match their published revision")
        return data

    async def return_file(self, scope: CallScope, *, name: str, expected_revision: str) -> TempFileView:
        await self._scope(scope)
        async with transaction(self.database.control_sessions) as tx:
            plan = await A2ATempFileService(tx).target_file(tenant_id=scope.tenant_id, run_id=scope.run_id, name=name)
        async with self.storage.guard(plan.storage_key):
            await self._read(plan)
            async with transaction(self.database.control_sessions) as tx:
                return (await A2ATempFileService(tx).return_file(tenant_id=scope.tenant_id, run_id=scope.run_id,
                    name=name, expected_revision=expected_revision)).file

    async def save(self, scope: CallScope, *, request_id: UUID, name: str, path: str,
            expected_revision: str | None, operation: str) -> str:
        await self._scope(scope)
        if not path.startswith("files/"):
            raise InvalidInput("Returned files may be saved only under the current output files/")
        async with transaction(self.database.control_sessions) as tx:
            snapshot = await RunService(tx).read_snapshot(tenant_id=scope.tenant_id, run_id=scope.run_id)
            receipt = SaveReceipt(run_id=str(scope.run_id), operation=operation, subject_kind=snapshot.workspace.output.kind,
                subject_id=str(snapshot.workspace.output.id), path=path, expected_revision=expected_revision)
            plan = await A2ATempFileService(tx).prepare_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                request_id=request_id, name=name, receipt=receipt)
        if plan.file.save and plan.file.save.revision is not None:
            return plan.file.save.revision
        reconcile_previous = plan.resuming_save
        async with self.storage.guard(plan.storage_key):
            async with transaction(self.database.control_sessions) as tx:
                plan = await A2ATempFileService(tx).prepare_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                    request_id=request_id, name=name, receipt=receipt)
            if plan.file.save and plan.file.save.revision is not None:
                return plan.file.save.revision
            data = await self._read(plan)
            # An unresolved previous save may have written before its receipt transaction failed.
            # Matching bytes are an observed destination fact, never permission to overwrite a conflict.
            if reconcile_previous:
                from app.infrastructure.errors import NotFound
                try:
                    current = await self.workspace.read(snapshot.workspace, snapshot.workspace.output, path)
                except NotFound:
                    current = None
                if current is not None and current.content == data:
                    async with transaction(self.database.control_sessions) as tx:
                        await A2ATempFileService(tx).confirm_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                            request_id=request_id, name=name, receipt=receipt, revision=current.revision)
                    return current.revision
            # The Workspace write is drained before a cancellation can release the temporary file guard.
            try:
                revision = await _drain_write(self.workspace.write(snapshot.workspace, snapshot.workspace.output,
                    path, data, expected_revision=expected_revision))
            except FileConflict:
                async with transaction(self.database.control_sessions) as tx:
                    await A2ATempFileService(tx).reject_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                        request_id=request_id, name=name, receipt=receipt)
                raise
            async with transaction(self.database.control_sessions) as tx:
                await A2ATempFileService(tx).confirm_save(tenant_id=scope.tenant_id, run_id=scope.run_id,
                    request_id=request_id, name=name, receipt=receipt, revision=revision)
            return revision

    async def cleanup_once(self, *, after_id: UUID | None = None, limit: int = 100) -> UUID | None:
        async with transaction(self.database.control_sessions) as tx:
            requests = await A2ATempFileService(tx).cleanup_candidates(after_id=after_id, limit=limit)
        for tenant_id, request_id in requests:
            try:
                async with transaction(self.database.control_sessions) as tx:
                    plans = await A2ATempFileService(tx).files(tenant_id=tenant_id, request_id=request_id)
            except (DomainError, SQLAlchemyError) as error:
                self.cleanup_failures += 1
                logger.warning("A2A temporary manifest cleanup skipped: %s", type(error).__name__)
                continue
            for plan in plans:
                try:
                    await self._cleanup_file(plan)
                except (DomainError, OSError, SQLAlchemyError) as error:
                    self.cleanup_failures += 1
                    logger.warning("A2A temporary file cleanup skipped: %s", type(error).__name__)
        return requests[-1][1] if len(requests) == limit else None

    async def _cleanup_file(self, plan: TempFilePlan) -> None:
        async with self.storage.guard(plan.storage_key):
            async with transaction(self.database.control_sessions) as tx:
                claimed = await A2ATempFileService(tx).claim_cleanup(tenant_id=plan.tenant_id, request_id=plan.request_id,
                    name=plan.file.name)
            if claimed is None:
                return
            async with self._io:
                stored = await self.storage.inspect(claimed.storage_key)
                if stored is not None:
                    known = (stored.revision, stored.byte_size, stored.sha256) == (
                        claimed.file.revision, claimed.file.byte_size, claimed.file.sha256)
                    pending = claimed.file.pending
                    if not known and (pending is None or (stored.byte_size, stored.sha256) != (pending.byte_size, pending.sha256)):
                        raise Conflict("Cleanup refuses an unrecorded temporary revision")
                    if not await _drain_write(self.storage.delete(claimed.storage_key, revision=stored.revision)):
                        return
            async with transaction(self.database.control_sessions) as tx:
                await A2ATempFileService(tx).finish_cleanup(claimed)
