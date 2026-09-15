from hashlib import sha256
from uuid import uuid4

import pytest
from modules.a2a.test_service import accept, setup
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput
from app.modules.a2a.public import A2AService, A2ATempFileService, Publication, SaveReceipt, TempStoredFile
from app.modules.run.public import InputContent, RunService, SourceIdentity, derive_child


async def family(transaction_factory):
    principal, source, target_agent = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target_agent)
    target = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=target_agent, run_id=target,
            snapshot=with_tools(snapshot(principal.tenant_id, target_agent, target), "send_message_to_agent"), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
    return principal, source, target, request


def publication(*, expected=None, size=4, operation="write"):
    return Publication(operation=operation, expected_revision=expected, byte_size=size, sha256=sha256(b"data").hexdigest(), media_type="text/plain")


async def test_return_freezes_exact_revision_and_cleanup_requires_source_confirmation(transaction_factory):
    p, source, target, request = await family(transaction_factory)
    prepared = publication()
    async with transaction_factory() as tx:
        owner = A2ATempFileService(tx)
        await owner.prepare_write(tenant_id=p.tenant_id, run_id=target, name="result.txt", publication=prepared)
        with pytest.raises(Conflict):
            await owner.target_file(tenant_id=p.tenant_id, run_id=target, name="result.txt")
        await owner.publish(tenant_id=p.tenant_id, run_id=target, name="result.txt", publication=prepared,
            stored=TempStoredFile("revision", 4, prepared.sha256))
        await owner.return_file(tenant_id=p.tenant_id, run_id=target, name="result.txt", expected_revision="revision")
        with pytest.raises(Conflict):
            await owner.prepare_write(tenant_id=p.tenant_id, run_id=target, name="result.txt", publication=publication(expected="revision"))
        with pytest.raises(AccessDenied):
            await owner.source_file(tenant_id=p.tenant_id, run_id=target, request_id=request.id, name="result.txt")
        disclosed = await A2AService(tx).returned_files_for_source(tenant_id=p.tenant_id,
            source_run_id=source.id, request_id=request.id)
        assert len(disclosed) == 1 and disclosed[0].name == "result.txt" and disclosed[0].revision == "revision"
        assert not hasattr(disclosed[0], "storage_key") and not hasattr(disclosed[0], "save")
        with pytest.raises(AccessDenied):
            await A2AService(tx).returned_files_for_source(tenant_id=p.tenant_id, source_run_id=target, request_id=request.id)
        assert (await owner.source_file(tenant_id=p.tenant_id, run_id=source.id, request_id=request.id, name="result.txt")).file.returned
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=target, status="Failed", reason="done")
    async with transaction_factory() as tx:
        assert await A2ATempFileService(tx).claim_cleanup(tenant_id=p.tenant_id, request_id=request.id, name="result.txt") is None
    receipt = SaveReceipt(run_id=str(source.id), operation="save", subject_kind="agent", subject_id=str(source.agent_id),
        path="files/result.txt", expected_revision=None)
    async with transaction_factory() as tx:
        owner = A2ATempFileService(tx)
        await owner.prepare_save(tenant_id=p.tenant_id, run_id=source.id, request_id=request.id, name="result.txt", receipt=receipt)
        assert await owner.claim_cleanup(tenant_id=p.tenant_id, request_id=request.id, name="result.txt") is None
        await owner.confirm_save(tenant_id=p.tenant_id, run_id=source.id, request_id=request.id, name="result.txt", receipt=receipt, revision="saved")
    async with transaction_factory() as tx:
        owner = A2ATempFileService(tx)
        claimed = await owner.claim_cleanup(tenant_id=p.tenant_id, request_id=request.id, name="result.txt")
        assert claimed is not None
        await owner.finish_cleanup(claimed)
        assert (await owner.prepare_save(tenant_id=p.tenant_id, run_id=source.id, request_id=request.id, name="result.txt", receipt=receipt)).file.save.revision == "saved"


async def test_pending_failed_publication_remains_owned_and_cleanup_blocks_late_write(transaction_factory):
    p, _, target, request = await family(transaction_factory)
    prepared = publication()
    async with transaction_factory() as tx:
        await A2ATempFileService(tx).prepare_write(tenant_id=p.tenant_id, run_id=target, name="work.txt", publication=prepared)
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=target, status="Cancelled", reason="cancel")
    async with transaction_factory() as tx:
        owner = A2ATempFileService(tx)
        claimed = await owner.claim_cleanup(tenant_id=p.tenant_id, request_id=request.id, name="work.txt")
        assert claimed and claimed.file.pending == prepared
        with pytest.raises(AccessDenied):
            await owner.publish(tenant_id=p.tenant_id, run_id=target, name="work.txt", publication=prepared,
                stored=TempStoredFile("late", 4, prepared.sha256))


@pytest.mark.parametrize("invalid", ["path", "count", "total"])
async def test_temporary_manifest_bounds_are_enforced(transaction_factory, invalid):
    p, _, target, _ = await family(transaction_factory)
    if invalid == "path":
        with pytest.raises(InvalidInput):
            async with transaction_factory() as tx:
                await A2ATempFileService(tx).prepare_write(tenant_id=p.tenant_id, run_id=target, name="../escape", publication=publication())
        return
    count, size = (8, 1) if invalid == "count" else (4, 4 * 1024 * 1024)
    for index in range(count):
        async with transaction_factory() as tx:
            await A2ATempFileService(tx).prepare_write(tenant_id=p.tenant_id, run_id=target, name=str(index), publication=publication(size=size))
    with pytest.raises(InvalidInput):
        async with transaction_factory() as tx:
            await A2ATempFileService(tx).prepare_write(tenant_id=p.tenant_id, run_id=target, name="extra", publication=publication())


async def test_target_child_uses_parent_request_but_cannot_act_as_source(transaction_factory):
    p, _, target, request = await family(transaction_factory)
    child = uuid4()
    async with transaction_factory() as tx:
        parent_snapshot = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=target)
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=parent_snapshot.agent_id, run_id=child,
            snapshot=derive_child(parent_snapshot, run_id=child), source=SourceIdentity("task", target, "child"),
            input=InputContent("Process temporary data"), parent_run_id=target)
        owner = A2ATempFileService(tx)
        plan = await owner.prepare_write(tenant_id=p.tenant_id, run_id=child, name="child.txt", publication=publication())
        assert plan.request_id == request.id
        await owner.publish(tenant_id=p.tenant_id, run_id=child, name="child.txt", publication=publication(),
            stored=TempStoredFile("child-revision", 4, publication().sha256))
        assert (await owner.target_file(tenant_id=p.tenant_id, run_id=target, name="child.txt")).file.revision == "child-revision"
        with pytest.raises(AccessDenied):
            await owner.source_file(tenant_id=p.tenant_id, run_id=child, request_id=request.id, name="child.txt")
