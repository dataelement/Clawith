"""Real Session attachment ownership/publication facts; storage bytes belong to app tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from modules.group.test_service import setup as group_setup
from modules.run.test_lifecycle import snapshot

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.modules.run.public import InputContent, InputReference, RunService, SourceIdentity, derive_child
from app.modules.session.attachments import SessionAttachmentService
from app.modules.session.public import SessionConsumers, SessionService
from app.modules.workspace.public import WorkspaceSubject

HASH = sha256(b"text").hexdigest()


async def setup(transaction_factory):
    principal, peer, _, agent = await group_setup(transaction_factory)
    async with transaction_factory() as tx:
        session = await SessionService(tx).create(principal, agent_id=agent)
    return principal, peer, session, agent


async def upload(transaction_factory, principal, session, key="upload", *, now=None, publish=True):
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        result = await service.begin_upload(principal, session_id=session.id, upload_source_key=key,
            filename="report.txt", media_type="text/plain", byte_size=4, sha256=HASH, now=now)
    if publish:
        async with transaction_factory() as tx:
            await SessionAttachmentService(tx).publish_upload(principal, session_id=session.id, attachment_id=result.view.id,
                revision="revision", byte_size=4, sha256=HASH, now=now)
    async with transaction_factory() as tx:
        return await SessionAttachmentService(tx).get_upload(principal, session_id=session.id, attachment_id=result.view.id, now=now)


async def bind(transaction_factory, principal, session, blob, key="input"):
    async with transaction_factory() as tx:
        accepted = await SessionService(tx).accept_input(principal, session_id=session.id, source_key=key,
            input=InputContent("Read file", (InputReference(blob.view.reference),)))
        await SessionAttachmentService(tx).bind_to_input(principal, session_id=session.id, input_id=accepted.entry.id,
            attachment_ids=(blob.view.id,))
        return accepted


async def test_published_upload_deduplication_immutable_revision_and_private_session(transaction_factory):
    principal, peer, session, _ = await setup(transaction_factory)
    first = await upload(transaction_factory, principal, session)
    repeated = await upload(transaction_factory, principal, session)
    assert first == repeated and first.view.published_at is not None
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        with pytest.raises(AccessDenied):
            await service.authorize_read(peer, session_id=session.id, attachment_id=first.view.id)
        with pytest.raises(NotFound):
            await service.authorize_read(replace(principal, tenant_id=uuid4()), session_id=session.id, attachment_id=first.view.id)
        with pytest.raises(Conflict):
            await service.publish_upload(principal, session_id=session.id, attachment_id=first.view.id,
                revision="changed", byte_size=4, sha256=HASH)
        with pytest.raises(Conflict):
            await service.begin_upload(principal, session_id=session.id, upload_source_key="upload",
                filename="different", media_type="text/plain", byte_size=4, sha256=HASH)


async def test_unpublished_attachment_rejects_atomic_input_binding(transaction_factory):
    principal, _, session, _ = await setup(transaction_factory)
    blob = await upload(transaction_factory, principal, session, publish=False)
    with pytest.raises(Conflict):
        await bind(transaction_factory, principal, session, blob)
    async with transaction_factory() as tx:
        assert (await SessionService(tx).get(principal, session_id=session.id)).through_position == 0
        assert (await SessionAttachmentService(tx).get_upload(principal, session_id=session.id, attachment_id=blob.view.id)).view.origin_input_id is None


async def test_fixed_cutoff_explicit_related_reference_and_child_inheritance(transaction_factory):
    principal, _, session, agent = await setup(transaction_factory)
    first = await upload(transaction_factory, principal, session, "first")
    accepted = await bind(transaction_factory, principal, session, first, "first-input")
    run_id, child_id = uuid4(), uuid4()
    captured = snapshot(principal.tenant_id, agent, run_id)
    async with transaction_factory() as tx:
        runs = RunService(tx)
        await runs.start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id, snapshot=captured,
            source=SourceIdentity("session", session.id, str(accepted.link.id)), input=accepted.entry.content,
            start_consumer=SessionConsumers())
        await runs.start(tenant_id=principal.tenant_id, agent_id=agent, run_id=child_id,
            snapshot=derive_child(captured, run_id=child_id), source=SourceIdentity("task", run_id, "child"),
            input=InputContent("Read parent input"), parent_run_id=run_id)
    later = await upload(transaction_factory, principal, session, "later")
    await bind(transaction_factory, principal, session, later, "later-input")
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=first.view.id)).view.id == first.view.id
        assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=child_id, attachment_id=first.view.id)).view.id == first.view.id
        with pytest.raises(AccessDenied):
            await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=later.view.id)
        await RunService(tx).append_related(tenant_id=principal.tenant_id, run_id=run_id,
            input=InputContent("Use this new file", (InputReference(later.view.reference),)),
            source=SourceIdentity("session_input", session.id, "explicit-file"))
        assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=child_id, attachment_id=later.view.id)).view.id == later.view.id


async def test_expired_cleanup_is_unbound_only_and_conditioned_on_revision(transaction_factory):
    principal, _, session, _ = await setup(transaction_factory)
    stamp = datetime.now(UTC)
    bound = await upload(transaction_factory, principal, session, "bound", now=stamp)
    await bind(transaction_factory, principal, session, bound)
    staged = await upload(transaction_factory, principal, session, "orphan", now=stamp)
    expired = stamp + timedelta(hours=25)
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        page = await service.expired_unbound(now=expired)
        assert [item.view.id for item in page] == [staged.view.id]
        assert not await service.finish_cleanup(replace(staged, storage_revision="not-the-observed-revision"), now=expired)
        assert await service.claim_cleanup(bound, now=expired) is None
        assert not await service.finish_cleanup(bound, now=expired)
        claimed = await service.claim_cleanup(staged, now=expired)
        assert claimed is not None
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        recovered = await service.expired_unbound(now=expired)
        assert recovered == (claimed,)
        assert await service.finish_cleanup(claimed, now=expired)
        assert not await service.finish_cleanup(claimed, now=expired)
        assert (await service.authorize_read(principal, session_id=session.id, attachment_id=bound.view.id, now=expired)).view.id == bound.view.id


@pytest.mark.parametrize("size,valid", [(0, True), (4194304, True), (4194305, False), (-1, False), (True, False)])
async def test_upload_bound_and_storage_path_never_follow_filename(transaction_factory, size, valid):
    principal, _, session, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        if valid:
            blob = await service.begin_upload(principal, session_id=session.id, upload_source_key="size", filename="../soul.md",
                media_type="text/plain", byte_size=size, sha256=HASH)
            assert ".." not in blob.storage_key and blob.view.filename == "../soul.md"
        else:
            with pytest.raises(InvalidInput):
                await service.begin_upload(principal, session_id=session.id, upload_source_key="size", filename="file",
                    media_type="text/plain", byte_size=size, sha256=HASH)


async def test_concurrent_registration_and_publication_keep_one_identity(transaction_factory):
    principal, _, session, _ = await setup(transaction_factory)
    first, second = await asyncio.gather(*(upload(transaction_factory, principal, session) for _ in range(2)))
    assert first == second
    async with transaction_factory() as tx:
        service = SessionAttachmentService(tx)
        with pytest.raises(Conflict):
            await service.publish_upload(principal, session_id=session.id, attachment_id=first.view.id,
                revision="revision", byte_size=99, sha256=HASH)


async def test_invalid_batch_does_not_partially_bind_when_caller_handles_error(transaction_factory):
    principal, _, session, _ = await setup(transaction_factory)
    first = await upload(transaction_factory, principal, session, "one")
    second = await upload(transaction_factory, principal, session, "two")
    async with transaction_factory() as tx:
        accepted = await SessionService(tx).accept_input(principal, session_id=session.id, source_key="partial",
            input=InputContent("Only one declared file", (InputReference(first.view.reference),)))
        service = SessionAttachmentService(tx)
        with pytest.raises(InvalidInput):
            await service.bind_to_input(principal, session_id=session.id, input_id=accepted.entry.id,
                attachment_ids=(first.view.id, second.view.id))
        assert (await service.get_upload(principal, session_id=session.id, attachment_id=first.view.id)).view.origin_input_id is None
        assert (await service.get_upload(principal, session_id=session.id, attachment_id=second.view.id)).view.origin_input_id is None


@pytest.mark.parametrize("claim_first", [True, False])
async def test_cleanup_claim_and_binding_serialize_at_the_attachment_row(transaction_factory, claim_first):
    principal, _, session, _ = await setup(transaction_factory)
    stamp = datetime.now(UTC)
    blob = await upload(transaction_factory, principal, session, now=stamp)
    async with transaction_factory() as tx:
        accepted = await SessionService(tx).accept_input(principal, session_id=session.id, source_key="race",
            input=InputContent("File", (InputReference(blob.view.reference),)))
    started = asyncio.Event()
    async def bind_later():
        try:
            async with transaction_factory() as tx:
                started.set()
                return await SessionAttachmentService(tx).bind_to_input(principal, session_id=session.id,
                    input_id=accepted.entry.id, attachment_ids=(blob.view.id,), now=stamp)
        except Conflict:
            return "claimed"
    async def claim_later():
        async with transaction_factory() as tx:
            started.set()
            return await SessionAttachmentService(tx).claim_cleanup(blob, now=stamp + timedelta(hours=25))
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        if claim_first:
            async with transaction_factory() as tx:
                claimed = await SessionAttachmentService(tx).claim_cleanup(blob, now=stamp + timedelta(hours=25))
                assert claimed is not None
                waiting = tasks.create_task(bind_later())
                await started.wait()
                await asyncio.sleep(.01)
                assert not waiting.done()
            assert await waiting == "claimed"
            async with transaction_factory() as tx:
                with pytest.raises(Conflict):
                    await SessionAttachmentService(tx).authorize_read(principal, session_id=session.id,
                        attachment_id=blob.view.id, now=stamp)
                with pytest.raises(Conflict):
                    await SessionAttachmentService(tx).publish_upload(principal, session_id=session.id,
                        attachment_id=blob.view.id, revision="revision", byte_size=4, sha256=HASH, now=stamp)
        else:
            async with transaction_factory() as tx:
                await SessionAttachmentService(tx).bind_to_input(principal, session_id=session.id,
                    input_id=accepted.entry.id, attachment_ids=(blob.view.id,), now=stamp)
                waiting = tasks.create_task(claim_later())
                await started.wait()
                await asyncio.sleep(.01)
                assert not waiting.done()
            assert await waiting is None


@pytest.mark.parametrize("filename,media_type", [("file", "not-a-mime"), ("file", "😀"),
    ("file", "text/plain\r\nX: bad"), ("\ud800.txt", "text/plain")])
async def test_invalid_metadata_uses_stable_domain_error(transaction_factory, filename, media_type):
    principal, _, session, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await SessionAttachmentService(tx).begin_upload(principal, session_id=session.id,
                upload_source_key="invalid", filename=filename, media_type=media_type, byte_size=4, sha256=HASH)


@pytest.mark.parametrize("source_kind", ["trigger", "heartbeat"])
@pytest.mark.parametrize("scope_kind", ["matched", "agent", "other", "no_ref"])
async def test_scheduled_file_access_requires_explicit_ref_and_matching_private_subject(transaction_factory, source_kind, scope_kind):
    principal, _, session, agent = await setup(transaction_factory)
    blob = await upload(transaction_factory, principal, session)
    await bind(transaction_factory, principal, session, blob)
    run_id = uuid4()
    captured = snapshot(principal.tenant_id, agent, run_id)
    if scope_kind != "agent":
        captured = replace(captured, workspace=replace(captured.workspace,
            output=WorkspaceSubject("membership", uuid4() if scope_kind == "other" else principal.membership_id)))
    references = () if scope_kind == "no_ref" else (InputReference(blob.view.reference),)
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id, snapshot=captured,
            input=InputContent("Scheduled file", references), source=SourceIdentity(source_kind, uuid4(), "occurrence"))
        service = SessionAttachmentService(tx)
        if scope_kind == "matched":
            assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)).view.id == blob.view.id
        else:
            with pytest.raises(AccessDenied):
                await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)
