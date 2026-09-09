"""Run-created Group files bind to real messages, not a fabricated human input."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from modules.group.test_service import setup, started
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput
from app.modules.group.attachments import GroupAttachmentService
from app.modules.group.public import GroupService
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, InputReference, ModelStepPayload, RunService, SourceIdentity


async def scheduled_run(factory, principal, agent):
    identity = uuid4()
    async with factory() as tx:
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=identity,
            snapshot=with_tools(snapshot(principal.tenant_id, agent, identity), "send_message"), input=InputContent("Scheduled work"),
            source=SourceIdentity("trigger", uuid4(), "occurrence"))).run
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=identity,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("send", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
    return run


async def test_scheduled_group_file_has_real_creator_and_survives_cleanup_without_human_input(transaction_factory):
    p, _, group, agent = await setup(transaction_factory)
    run = await scheduled_run(transaction_factory, p, agent)
    async with transaction_factory() as tx:
        topic = await GroupService(tx).resolve_conversation(p, group_id=group.id)

    async def authorize(tx, *, run, target_id, conversation_id, input):
        if target_id != group.id or conversation_id != topic or run.source.kind != "trigger":
            raise AccessDenied("Destination differs from the explicitly authorized Group")

    digest = sha256(b"report").hexdigest()
    async with transaction_factory() as tx:
        files = GroupAttachmentService(tx)
        with pytest.raises(AccessDenied):
            await files.begin_run_upload(run=run, group_id=group.id, conversation_id=topic, step_id="step", call_id="send",
                upload_source_key="message:report", filename="report.txt", media_type="text/plain", byte_size=6, sha256=digest)
        blob = await files.begin_run_upload(run=run, group_id=group.id, conversation_id=topic, step_id="step", call_id="send",
            upload_source_key="message:report", filename="report.txt", media_type="text/plain", byte_size=6, sha256=digest, authorize=authorize)
        assert blob.view.uploader_membership_id is None and blob.view.created_by_run_id == run.id
        assert blob.view.origin_event_id is None and blob.view.bound_message_id is None
        published = await files.publish_run_upload(run=run, group_id=group.id, conversation_id=topic, attachment_id=blob.view.id,
            revision="revision-1", byte_size=6, sha256=digest, authorize=authorize)
        message = await GroupService(tx).accept_external_message(run=run, group_id=group.id, conversation_id=topic,
            step_id="step", call_id="send", input=InputContent("Report", (InputReference(published.reference),)), authorize=authorize)
        bound = await files.bind_to_message(run=run, group_id=group.id, message_id=message.id, attachment_ids=(published.id,))
        assert bound[0].bound_message_id == message.id and bound[0].origin_event_id is None
    async with transaction_factory() as tx:
        files = GroupAttachmentService(tx)
        assert (await files.authorize_delivery(tenant_id=p.tenant_id, agent_id=agent,
            message_id=message.id, attachment_id=published.id)).view.created_by_run_id == run.id
        assert (await files.authorize_read(p, group_id=group.id, attachment_id=published.id)).view.bound_message_id == message.id
        assert (await files.authorize_run_read(tenant_id=p.tenant_id, run_id=run.id, attachment_id=published.id)).view.id == published.id
        later = datetime.now(UTC) + timedelta(days=2)
        assert not await files.expired_unbound(now=later)
        assert await files.claim_cleanup(blob, now=later) is None


async def test_normal_group_generated_file_cannot_be_claimed_by_another_run(transaction_factory):
    p, _, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, p, group, agent)
    other = await scheduled_run(transaction_factory, p, agent)
    async with transaction_factory() as tx:
        topic = await GroupService(tx).resolve_conversation(p, group_id=group.id)
        files = GroupAttachmentService(tx)
        digest = sha256(b"data").hexdigest()
        original = await files.begin_run_upload(run=run, group_id=group.id, conversation_id=topic,
            step_id="message-step", call_id="send", upload_source_key="message:normal", filename="data.bin",
            media_type="application/octet-stream", byte_size=4, sha256=digest)
        async def authorize(tx, **kwargs):
            return None
        with pytest.raises(AccessDenied):
            await files.get_run_upload(run=other, group_id=group.id, conversation_id=topic,
                attachment_id=original.view.id, authorize=authorize)
        with pytest.raises(AccessDenied):
            await files.publish_run_upload(run=other, group_id=group.id, conversation_id=topic,
                attachment_id=original.view.id, revision="r", byte_size=4, sha256=digest, authorize=authorize)
        with pytest.raises(AccessDenied):
            await files.get_upload(p, group_id=group.id, attachment_id=original.view.id)
        await files.publish_run_upload(run=run, group_id=group.id, conversation_id=topic,
            attachment_id=original.view.id, revision="r", byte_size=4, sha256=digest)
        with pytest.raises(Conflict):
            await files.publish_run_upload(run=run, group_id=group.id, conversation_id=topic,
                attachment_id=original.view.id, revision="changed", byte_size=4, sha256=digest)
        other_message = await GroupService(tx).accept_external_message(run=other, group_id=group.id, conversation_id=topic,
            step_id="step", call_id="send", input=InputContent("Other Run", (InputReference(original.view.reference),)), authorize=authorize)
        with pytest.raises(AccessDenied):
            await files.bind_to_message(run=other, group_id=group.id, message_id=other_message.id, attachment_ids=(original.view.id,))
        own_message = await GroupService(tx).accept_message(tenant_id=p.tenant_id, run_id=run.id,
            step_id="message-step", call_id="send", input=InputContent("Own file", (InputReference(original.view.reference),)))
        bound = await files.bind_to_message(run=run, group_id=group.id, message_id=own_message.id, attachment_ids=(original.view.id,))
        assert bound[0].created_by_run_id == run.id and bound[0].origin_event_id is None and bound[0].bound_message_id == own_message.id
        with pytest.raises(AccessDenied):
            await files.authorize_delivery(tenant_id=p.tenant_id, agent_id=agent,
                message_id=other_message.id, attachment_id=original.view.id)


async def test_claimed_run_upload_cannot_bind_even_with_old_timestamp(transaction_factory):
    p, _, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, p, group, agent)
    now = datetime.now(UTC)
    async with transaction_factory() as tx:
        topic = await GroupService(tx).resolve_conversation(p, group_id=group.id)
        files = GroupAttachmentService(tx)
        digest = sha256(b"data").hexdigest()
        original = await files.begin_run_upload(run=run, group_id=group.id, conversation_id=topic,
            step_id="message-step", call_id="send", upload_source_key="message:cleanup", filename="data.bin",
            media_type="application/octet-stream", byte_size=4, sha256=digest, now=now)
        await files.publish_run_upload(run=run, group_id=group.id, conversation_id=topic,
            attachment_id=original.view.id, revision="r", byte_size=4, sha256=digest, now=now)
        observed = await files.get_run_upload(run=run, group_id=group.id, conversation_id=topic, attachment_id=original.view.id, now=now)
    async with transaction_factory() as tx:
        assert await GroupAttachmentService(tx).claim_cleanup(observed, now=now + timedelta(days=2)) is not None
    async with transaction_factory() as tx:
        message = await GroupService(tx).accept_message(tenant_id=p.tenant_id, run_id=run.id,
            step_id="message-step", call_id="send", input=InputContent("File", (InputReference(original.view.reference),)))
        with pytest.raises(Conflict):
            await GroupAttachmentService(tx).bind_to_message(run=run, group_id=group.id,
                message_id=message.id, attachment_ids=(original.view.id,), now=now)


@pytest.mark.parametrize("count", [4, 5])
async def test_message_attachment_total_size_boundary_and_count_limit(transaction_factory, count):
    p, _, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, p, group, agent)
    async with transaction_factory() as tx:
        owner = GroupService(tx)
        topic = await owner.resolve_conversation(p, group_id=group.id)
        files = GroupAttachmentService(tx)
        ids, refs = [], []
        for index in range(count):
            plan = await files.begin_run_upload(run=run, group_id=group.id, conversation_id=topic,
                step_id="message-step", call_id="send", upload_source_key=f"message:quota:{index}", filename=f"{index}.bin",
                media_type="application/octet-stream", byte_size=4 * 1024 * 1024, sha256="a" * 64)
            await files.publish_run_upload(run=run, group_id=group.id, conversation_id=topic,
                attachment_id=plan.view.id, revision="r", byte_size=4 * 1024 * 1024, sha256="a" * 64)
            ids.append(plan.view.id)
            refs.append(InputReference(plan.view.reference))
        message = await owner.accept_message(tenant_id=p.tenant_id, run_id=run.id, step_id="message-step", call_id="send",
            input=InputContent("Files", tuple(refs)))
        with pytest.raises(InvalidInput, match="count"):
            await files.bind_to_message(run=run, group_id=group.id, message_id=message.id,
                attachment_ids=tuple(uuid4() for _ in range(9)))
        if count == 5:
            with pytest.raises(InvalidInput, match="sixteen"):
                await files.bind_to_message(run=run, group_id=group.id, message_id=message.id, attachment_ids=tuple(ids))
        else:
            assert len(await files.bind_to_message(run=run, group_id=group.id,
                message_id=message.id, attachment_ids=tuple(ids))) == 4
