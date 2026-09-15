import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from modules.a2a.test_temp_files import family
from modules.run.test_lifecycle import snapshot
from modules.workspace.test_service import Observations

from app.execution_dependencies.a2a_temp_files import A2ATempFiles
from app.infrastructure.errors import Conflict
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.temp_files import TempFileStorage
from app.modules.a2a.public import A2AService, A2ATempFileService, Publication
from app.modules.agent.models import AgentRecord
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity
from app.modules.tool.public import CallScope
from app.modules.workspace.public import FileConflict, WorkspaceService


@pytest.mark.parametrize("cancel_save", [False, True])
async def test_source_save_conflict_or_cancel_never_discards_unsaved_return(
        test_database, transaction_factory, tmp_path, monkeypatch, cancel_save):
    p, source, target, request = await family(transaction_factory)
    backend = LocalStorageBackend(str(tmp_path))
    storage = TempFileStorage(backend)
    workspace = WorkspaceService(test_database.sessions, backend, Observations())
    async def no_attachment(scope, *, reference):
        pytest.fail("This test writes its own result and must not read delegated files")
    files = A2ATempFiles(SimpleNamespace(control_sessions=test_database.sessions), storage, workspace, no_attachment)
    async with transaction_factory() as tx:
        target_run = await RunService(tx).get(tenant_id=p.tenant_id, run_id=target)
        snapshot = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=source.id)
    await workspace.ensure(snapshot.workspace, snapshot.workspace.output)
    target_scope, source_scope = CallScope(p.tenant_id, target_run.agent_id, target), CallScope(p.tenant_id, source.agent_id, source.id)
    value = await files.write(target_scope, name="result.txt", content=b"result", media_type="text/plain", expected_revision=None, operation="write")
    await files.return_file(target_scope, name="result.txt", expected_revision=value.revision)
    try:
        if not cancel_save:
            existing = await workspace.write(snapshot.workspace, snapshot.workspace.output, "files/result.txt", b"other", expected_revision=None)
            with pytest.raises(FileConflict):
                await files.save(source_scope, request_id=request.id, name="result.txt", path="files/result.txt", expected_revision=None, operation="save")
            assert (await workspace.read(snapshot.workspace, snapshot.workspace.output, "files/result.txt")).content == b"other"
            await files.cleanup_once()
            assert (await files.read(source_scope, request_id=request.id, name="result.txt"))[0] == b"result"
            await files.save(source_scope, request_id=request.id, name="result.txt", path="files/result.txt", expected_revision=existing, operation="retry")
        else:
            written, release = asyncio.Event(), asyncio.Event()
            original_write = workspace.write
            calls = 0
            async def delayed_write(*args, **kwargs):
                nonlocal calls
                calls += 1
                revision = await original_write(*args, **kwargs)
                written.set()
                await release.wait()
                return revision
            monkeypatch.setattr(workspace, "write", delayed_write)
            saving = asyncio.create_task(files.save(source_scope, request_id=request.id, name="result.txt",
                path="files/result.txt", expected_revision=None, operation="save"))
            await asyncio.wait_for(written.wait(), 3)
            saving.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await saving
            await files.cleanup_once()
            assert (await files.read(source_scope, request_id=request.id, name="result.txt"))[0] == b"result"
            await files.save(source_scope, request_id=request.id, name="result.txt", path="files/result.txt",
                expected_revision=None, operation="save")
            assert calls == 1
        async with transaction_factory() as tx:
            plans = await A2ATempFileService(tx).files(tenant_id=p.tenant_id, request_id=request.id)
        await files.cleanup_once()
        assert plans and await storage.inspect(plans[0].storage_key) is None
    finally:
        await backend.aclose()


async def test_nested_binary_return_copies_into_b_temporary_space_before_c_cleanup(
        test_database, transaction_factory, tmp_path):
    p, source, middle_run_id, outer_request = await family(transaction_factory)
    async with transaction_factory() as tx:
        middle = await RunService(tx).get(tenant_id=p.tenant_id, run_id=middle_run_id)
        source_agent = await tx.session.get(AgentRecord, source.agent_id)
        now = datetime.now(UTC)
        target = AgentRecord(id=uuid4(), tenant_id=p.tenant_id, model_id=source_agent.model_id,
            created_by_membership_id=p.membership_id, name="Third", soul="Process", timezone="UTC", enabled=True,
            created_at=now, updated_at=now)
        tx.session.add(target)
        await tx.session.flush()
        await PermissionService(tx).set_visibility(p, agent_id=target.id, visibility="tenant")
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=middle_run_id,
            payload=ModelStepPayload("send-c", 1, ModelStepResult("", (ModelToolCall("send", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "send-c", False)))
        request = await A2AService(tx).accept(tenant_id=p.tenant_id, source_run_id=middle_run_id,
            step_id="send-c", call_id="send", target_agent_id=target.id, intent="consult", input=InputContent("Binary result"))
        last = uuid4()
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target.id, run_id=last,
            snapshot=snapshot(p.tenant_id, target.id, last), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
    backend = LocalStorageBackend(str(tmp_path))
    storage = TempFileStorage(backend)
    workspace = WorkspaceService(test_database.sessions, backend, Observations())
    async def no_attachment(scope, *, reference):
        pytest.fail("Nested binary copy uses returned-file authorization, not input delegation")
    files = A2ATempFiles(SimpleNamespace(control_sessions=test_database.sessions), storage, workspace, no_attachment)
    try:
        data = b"\x00\xff\x80nested-binary"
        value = await files.write(CallScope(p.tenant_id, target.id, last), name="result.bin", content=data,
            media_type="application/octet-stream", expected_revision=None, operation="write")
        await files.return_file(CallScope(p.tenant_id, target.id, last), name="result.bin", expected_revision=value.revision)
        copied = await files.import_return(CallScope(p.tenant_id, middle.agent_id, middle_run_id), request_id=request.id,
            source_name="result.bin", name="nested.bin", expected_revision=None, operation="copy")
        assert (await files.read(CallScope(p.tenant_id, middle.agent_id, middle_run_id), name="nested.bin"))[0] == data
        async with transaction_factory() as tx:
            returned = (await A2ATempFileService(tx).returned_files(tenant_id=p.tenant_id, request_id=request.id))[0]
            assert returned.save.subject_kind == "a2a" and returned.save.subject_id == str(outer_request.id)
            assert returned.save.revision == copied.revision
        await files.cleanup_once()
        assert (await files.read(CallScope(p.tenant_id, middle.agent_id, middle_run_id), name="nested.bin"))[0] == data
    finally:
        await backend.aclose()


async def test_cleanup_does_not_delete_unknown_bytes_or_block_another_file(test_database, transaction_factory, tmp_path):
    p, _, target, request = await family(transaction_factory)
    async with transaction_factory() as tx:
        run = await RunService(tx).get(tenant_id=p.tenant_id, run_id=target)
    backend = LocalStorageBackend(str(tmp_path))
    storage = TempFileStorage(backend)
    async def no_attachment(scope, *, reference):
        pytest.fail("No attachment expected")
    files = A2ATempFiles(SimpleNamespace(control_sessions=test_database.sessions), storage,
        WorkspaceService(test_database.sessions, backend, Observations()), no_attachment)
    try:
        scope = CallScope(p.tenant_id, run.agent_id, target)
        for name in ("bad", "good"):
            await files.write(scope, name=name, content=b"known", media_type="text/plain", expected_revision=None, operation=name)
        async with transaction_factory() as tx:
            plans = await A2ATempFileService(tx).files(tenant_id=p.tenant_id, request_id=request.id)
            await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=target, status="Interrupted", reason="stop")
        bad, good = plans
        await backend.write_bytes(bad.storage_key, b"unrecorded")
        await files.cleanup_once()
        assert files.cleanup_failures == 1
        assert await storage.inspect(bad.storage_key) is not None
        assert await storage.inspect(good.storage_key) is None
    finally:
        await backend.aclose()


async def test_new_model_call_confirms_identical_pending_write_without_overwriting_newer_intent(
        test_database, transaction_factory, tmp_path, monkeypatch):
    p, _, target, request = await family(transaction_factory)
    async with transaction_factory() as tx:
        run = await RunService(tx).get(tenant_id=p.tenant_id, run_id=target)
    backend = LocalStorageBackend(str(tmp_path))
    storage = TempFileStorage(backend)
    async def no_attachment(scope, *, reference):
        pytest.fail("No attachment expected")
    files = A2ATempFiles(SimpleNamespace(control_sessions=test_database.sessions), storage,
        WorkspaceService(test_database.sessions, backend, Observations()), no_attachment)
    scope = CallScope(p.tenant_id, run.agent_id, target)
    actual_write = storage.write
    written = []
    async def write_then_fail(*args, **kwargs):
        result = await actual_write(*args, **kwargs)
        written.append(result)
        if len(written) == 1:
            raise OSError("Provider failed after storing bytes")
        return result
    monkeypatch.setattr(storage, "write", write_then_fail)
    try:
        with pytest.raises(OSError):
            await files.write(scope, name="result", content=b"bytes", media_type="text/plain", expected_revision=None, operation="old-call")
        async with transaction_factory() as tx:
            pending = (await A2ATempFileService(tx).files(tenant_id=p.tenant_id, request_id=request.id))[0].file.pending
        assert pending is not None and pending.operation == "old-call"
        with pytest.raises(Conflict, match="unresolved"):
            await files.write(scope, name="result", content=b"different", media_type="text/plain", expected_revision=None, operation="different-call")
        confirmed = await files.write(scope, name="result", content=b"bytes", media_type="text/plain", expected_revision=None, operation="new-model-call")
        assert confirmed.revision == written[0].revision == written[1].revision
        assert (await files.read(scope, name="result"))[0] == b"bytes"
        next_intent = Publication(operation="next-version", expected_revision=confirmed.revision,
            byte_size=confirmed.byte_size, sha256=confirmed.sha256, media_type="text/plain")
        async with transaction_factory() as tx:
            await A2ATempFileService(tx).prepare_write(tenant_id=p.tenant_id, run_id=target, name="result", publication=next_intent)
        async with transaction_factory() as tx:
            owner = A2ATempFileService(tx)
            with pytest.raises(Conflict, match="recorded intent"):
                await owner.publish(tenant_id=p.tenant_id, run_id=target, name="result", publication=pending, stored=written[0])
            assert (await owner.files(tenant_id=p.tenant_id, request_id=request.id))[0].file.pending == next_intent
        final = await files.write(scope, name="result", content=b"bytes", media_type="text/plain",
            expected_revision=confirmed.revision, operation="confirm-next")
        assert (await files.return_file(scope, name="result", expected_revision=final.revision)).returned
        with pytest.raises(Conflict, match="cannot change"):
            await files.write(scope, name="result", content=b"bytes", media_type="text/plain", expected_revision=final.revision, operation="after-return")
    finally:
        await backend.aclose()
