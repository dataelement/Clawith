"""Group files remain private until bound and obey each execution's fixed cutoff."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from modules.group.test_service import setup
from modules.run.test_lifecycle import snapshot

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.modules.group.attachments import GroupAttachmentService
from app.modules.group.public import GroupService
from app.modules.run.public import InputContent, InputReference, RunService, SourceIdentity, derive_child
from app.modules.workspace.public import WorkspaceSubject

HASH = sha256(b"text").hexdigest()


async def upload(transaction_factory, principal, group, key="upload", *, now=None, publish=True):
    async with transaction_factory() as tx:
        result = await GroupAttachmentService(tx).begin_upload(principal, group_id=group.id, upload_source_key=key,
            filename="group.txt", media_type="text/plain", byte_size=4, sha256=HASH, now=now)
    if publish:
        async with transaction_factory() as tx:
            await GroupAttachmentService(tx).publish_upload(principal, group_id=group.id, attachment_id=result.view.id,
                revision="revision", byte_size=4, sha256=HASH, now=now)
    async with transaction_factory() as tx:
        return await GroupAttachmentService(tx).get_upload(principal, group_id=group.id, attachment_id=result.view.id, now=now)


async def bind(transaction_factory, principal, group, blob, *, agent_ids=(), key="input"):
    async with transaction_factory() as tx:
        accepted = await GroupService(tx).accept_input(principal, group_id=group.id, source_key=key,
            input=InputContent("Read file", (InputReference(blob.view.reference),)), agent_ids=agent_ids)
        await GroupAttachmentService(tx).bind_to_input(principal, group_id=group.id, event_id=accepted.event.id,
            attachment_ids=(blob.view.id,))
        return accepted


async def test_members_cannot_read_or_claim_another_unsubmitted_upload(transaction_factory):
    principal, peer, group, _ = await setup(transaction_factory)
    blob = await upload(transaction_factory, principal, group)
    async with transaction_factory() as tx:
        await GroupService(tx).set_membership(principal, group_id=group.id, membership_id=peer.membership_id, enabled=True)
        service = GroupAttachmentService(tx)
        with pytest.raises(AccessDenied):
            await service.authorize_read(peer, group_id=group.id, attachment_id=blob.view.id)
        with pytest.raises(AccessDenied):
            await service.begin_upload(peer, group_id=group.id, upload_source_key="upload", filename="group.txt",
                media_type="text/plain", byte_size=4, sha256=HASH)
        with pytest.raises(AccessDenied):
            await service.publish_upload(peer, group_id=group.id, attachment_id=blob.view.id, revision="revision", byte_size=4, sha256=HASH)
    with pytest.raises(AccessDenied):
        await bind(transaction_factory, peer, group, blob)
    await bind(transaction_factory, principal, group, blob)
    async with transaction_factory() as tx:
        assert (await GroupAttachmentService(tx).authorize_read(peer, group_id=group.id, attachment_id=blob.view.id)).view.id == blob.view.id
        with pytest.raises(NotFound):
            await GroupAttachmentService(tx).authorize_read(replace(peer, tenant_id=uuid4()), group_id=group.id, attachment_id=blob.view.id)


async def test_group_cutoff_and_parent_permission_do_not_include_future_event_files(transaction_factory):
    principal, _, group, agent = await setup(transaction_factory)
    first = await upload(transaction_factory, principal, group, "first")
    accepted = await bind(transaction_factory, principal, group, first, agent_ids=(agent,), key="first-event")
    run_id, child_id = uuid4(), uuid4()
    captured = snapshot(principal.tenant_id, agent, run_id)
    async with transaction_factory() as tx:
        runs = RunService(tx)
        await runs.start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id, snapshot=captured,
            source=SourceIdentity("group", accepted.event.id, str(agent)), input=accepted.event.input, start_consumer=GroupService(tx))
        await runs.start(tenant_id=principal.tenant_id, agent_id=agent, run_id=child_id,
            snapshot=derive_child(captured, run_id=child_id), source=SourceIdentity("task", run_id, "child"), input=InputContent("work"), parent_run_id=run_id)
    later = await upload(transaction_factory, principal, group, "later")
    await bind(transaction_factory, principal, group, later, key="later-event")
    async with transaction_factory() as tx:
        service = GroupAttachmentService(tx)
        assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=child_id, attachment_id=first.view.id)).view.id == first.view.id
        with pytest.raises(AccessDenied):
            await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=child_id, attachment_id=later.view.id)
        await RunService(tx).append_related(tenant_id=principal.tenant_id, run_id=run_id,
            input=InputContent("Use new file", (InputReference(later.view.reference),)), source=SourceIdentity("group_input", group.id, "explicit-file"))
        assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=child_id, attachment_id=later.view.id)).view.id == later.view.id


async def test_binding_failure_rolls_back_event_and_cleanup_keeps_bound_files(transaction_factory):
    principal, _, group, _ = await setup(transaction_factory)
    stamp = datetime.now(UTC)
    staged = await upload(transaction_factory, principal, group, "staged", now=stamp, publish=False)
    with pytest.raises(Conflict):
        await bind(transaction_factory, principal, group, staged)
    async with transaction_factory() as tx:
        assert not await GroupService(tx).list_events(principal, group_id=group.id)
    bound = await upload(transaction_factory, principal, group, "bound", now=stamp)
    await bind(transaction_factory, principal, group, bound)
    expired = stamp + timedelta(hours=25)
    async with transaction_factory() as tx:
        service = GroupAttachmentService(tx)
        page = await service.expired_unbound(now=expired)
        assert [item.view.id for item in page] == [staged.view.id]
        with pytest.raises(Conflict):
            await service.get_upload(principal, group_id=group.id, attachment_id=staged.view.id, now=expired)
        assert not await service.finish_cleanup(bound, now=expired)
        assert await service.claim_cleanup(bound, now=expired) is None
        claimed = await service.claim_cleanup(staged, now=expired)
        assert claimed is not None
    async with transaction_factory() as tx:
        service = GroupAttachmentService(tx)
        assert (await service.expired_unbound(now=expired)) == (claimed,)
        assert await service.finish_cleanup(claimed, now=expired)


async def test_a2a_target_requires_an_explicit_owner_delegation_port(transaction_factory):
    principal, _, group, agent = await setup(transaction_factory)
    blob = await upload(transaction_factory, principal, group)
    await bind(transaction_factory, principal, group, blob)
    run_id = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id,
            snapshot=snapshot(principal.tenant_id, agent, run_id), source=SourceIdentity("a2a", uuid4(), "target"), input=InputContent("work"))
        with pytest.raises(AccessDenied):
            await GroupAttachmentService(tx).authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)
        async def reject(transaction, *, run, reference):
            raise AccessDenied("not explicitly delegated")
        with pytest.raises(AccessDenied):
            await GroupAttachmentService(tx, delegated_access=reject).authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)
        calls = []
        async def grant(transaction, *, run, reference):
            calls.append((run.id, reference))
        assert (await GroupAttachmentService(tx, delegated_access=grant).authorize_run_read(
            tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)).view.id == blob.view.id
        assert calls == [(run_id, blob.view.reference)]


async def test_claim_blocks_a_waiting_group_bind_even_with_a_pre_expiry_timestamp(transaction_factory):
    principal, _, group, _ = await setup(transaction_factory)
    stamp = datetime.now(UTC)
    blob = await upload(transaction_factory, principal, group, now=stamp)
    async with transaction_factory() as tx:
        accepted = await GroupService(tx).accept_input(principal, group_id=group.id, source_key="race",
            input=InputContent("File", (InputReference(blob.view.reference),)), agent_ids=())
    started = asyncio.Event()
    async def binder():
        try:
            async with transaction_factory() as tx:
                started.set()
                return await GroupAttachmentService(tx).bind_to_input(principal, group_id=group.id,
                    event_id=accepted.event.id, attachment_ids=(blob.view.id,), now=stamp)
        except Conflict:
            return "claimed"
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        async with transaction_factory() as tx:
            claimed = await GroupAttachmentService(tx).claim_cleanup(blob, now=stamp + timedelta(hours=25))
            assert claimed is not None
            waiting = tasks.create_task(binder())
            await started.wait()
            await asyncio.sleep(.01)
            assert not waiting.done()
        assert await waiting == "claimed"
    async with transaction_factory() as tx:
        service = GroupAttachmentService(tx)
        with pytest.raises(Conflict):
            await service.authorize_read(principal, group_id=group.id, attachment_id=blob.view.id, now=stamp)
        with pytest.raises(Conflict):
            await service.publish_upload(principal, group_id=group.id, attachment_id=blob.view.id,
                revision="revision", byte_size=4, sha256=HASH, now=stamp)


@pytest.mark.parametrize("filename,media_type", [("file", "not-a-mime"), ("file", "😀"),
    ("file", "text/plain\r\nX: bad"), ("\ud800.txt", "text/plain")])
async def test_invalid_metadata_uses_stable_domain_error(transaction_factory, filename, media_type):
    principal, _, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await GroupAttachmentService(tx).begin_upload(principal, group_id=group.id,
                upload_source_key="invalid", filename=filename, media_type=media_type, byte_size=4, sha256=HASH)


@pytest.mark.parametrize("source_kind", ["trigger", "heartbeat"])
@pytest.mark.parametrize("scope_kind", ["matched", "agent", "other", "no_ref"])
async def test_scheduled_file_access_requires_explicit_ref_and_matching_group_subject(transaction_factory, source_kind, scope_kind):
    principal, _, group, agent = await setup(transaction_factory)
    blob = await upload(transaction_factory, principal, group)
    await bind(transaction_factory, principal, group, blob)
    run_id = uuid4()
    captured = snapshot(principal.tenant_id, agent, run_id)
    if scope_kind != "agent":
        captured = replace(captured, workspace=replace(captured.workspace,
            output=WorkspaceSubject("group", uuid4() if scope_kind == "other" else group.id)))
    references = () if scope_kind == "no_ref" else (InputReference(blob.view.reference),)
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id, snapshot=captured,
            input=InputContent("Scheduled file", references), source=SourceIdentity(source_kind, uuid4(), "occurrence"))
        service = GroupAttachmentService(tx)
        if scope_kind == "matched":
            assert (await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)).view.id == blob.view.id
        else:
            with pytest.raises(AccessDenied):
                await service.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id, attachment_id=blob.view.id)
