import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.run.test_lifecycle import snapshot as base_snapshot
from modules.session.test_session import accept, setup
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, InputReference, ModelStepPayload, RunService, SourceIdentity
from app.modules.session.public import SessionAttachmentService, SessionConsumers, SessionService


def tool_snapshot(tenant, agent, identity):
    return with_tools(base_snapshot(tenant, agent, identity), "send_message")


async def started(factory, principal, session, receipt):
    identity = uuid4()
    async with factory() as tx:
        return (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=session.agent_id, run_id=identity,
            snapshot=tool_snapshot(principal.tenant_id, session.agent_id, identity), input=receipt.entry.content,
            source=SourceIdentity("session", session.id, str(receipt.link.id)), start_consumer=SessionConsumers())).run


async def scheduled(factory, kind="trigger"):
    principal, session = await setup(factory)
    identity = uuid4()
    async with factory() as tx:
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=session.agent_id, run_id=identity,
            snapshot=tool_snapshot(principal.tenant_id, session.agent_id, identity), input=InputContent("unattended work"),
            source=SourceIdentity(kind, uuid4(), "occurrence"))).run
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=identity,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("send", "send_message", '{"text":"result"}'),),
                "tool_calls", ModelUsage(), "response", False)))
    return principal, session, run


@pytest.mark.parametrize("kind", ["trigger", "heartbeat"])
async def test_external_message_keeps_actual_run_without_fabricated_session_input(transaction_factory, kind):
    principal, session, run = await scheduled(transaction_factory, kind)
    calls = []
    async def authorize(tx, *, run, target_id, conversation_id, input):
        calls.append((run.source.kind, target_id, conversation_id))
        if target_id != session.id or conversation_id is not None:
            raise AccessDenied("Destination does not match frozen occurrence")
    async with transaction_factory() as tx:
        owner = SessionService(tx)
        message = await owner.accept_external_message(run=run, session_id=session.id, step_id="step", call_id="send",
            input=InputContent("result"), authorize=authorize)
        repeated = await owner.accept_external_message(run=run, session_id=session.id, step_id="step", call_id="send",
            input=InputContent("retry must not replace"), authorize=authorize)
        assert message.entry == repeated.entry and not repeated.created
        assert message.entry.origin_input_id is None and message.entry.source_run_id == run.id
        assert await owner.get_message_for_delivery(tenant_id=run.tenant_id, agent_id=run.agent_id, message_id=message.entry.id) == message.entry
        assert await owner.find_external_message(run=run, session_id=session.id, step_id="step", call_id="send", authorize=authorize) == message.entry
        history = await owner.read_history(principal, session_id=session.id)
        assert len(history.entries) == 1 and history.entries[0].kind == "reply"
        assert not (await owner.list_work(principal, session_id=session.id)).work
        with pytest.raises(InvalidInput):
            await owner.accept_external_message(run=run, session_id=session.id, step_id="step", call_id="not-real",
                input=InputContent("forged"), authorize=authorize)
    assert len(calls) == 4


async def test_owner_invokes_destination_denial_without_relying_on_caller_preflight(transaction_factory):
    principal, session, run = await scheduled(transaction_factory)
    async def deny(*args, **kwargs):
        raise AccessDenied("Private origin cannot be published here")
    async with transaction_factory() as tx:
        with pytest.raises(AccessDenied):
            await SessionService(tx).accept_external_message(run=run, session_id=session.id, step_id="step", call_id="send",
                input=InputContent("private"), authorize=deny)
        assert not (await SessionService(tx).read_history(principal, session_id=session.id)).entries


async def test_concurrent_external_retries_allocate_one_committed_position(transaction_factory):
    principal, session, run = await scheduled(transaction_factory)
    async def authorize(tx, *, run, target_id, conversation_id, input):
        assert target_id == session.id
    async def send():
        async with transaction_factory() as tx:
            return await SessionService(tx).accept_external_message(run=run, session_id=session.id,
                step_id="step", call_id="send", input=InputContent("once"), authorize=authorize)
    results = await asyncio.gather(*(send() for _ in range(5)))
    assert sum(result.created for result in results) == 1 and len({result.entry.id for result in results}) == 1
    async with transaction_factory() as tx:
        assert len((await SessionService(tx).read_history(principal, session_id=session.id)).entries) == 1


async def test_run_files_bind_actual_message_retain_creator_and_survive_cleanup(transaction_factory):
    principal, session, run = await scheduled(transaction_factory)
    digest = hashlib.sha256(b"data").hexdigest()
    async def authorize(tx, *, run, target_id, conversation_id, input):
        assert target_id == session.id and conversation_id is None
    async with transaction_factory() as tx:
        files = SessionAttachmentService(tx)
        with pytest.raises(AccessDenied):
            await files.begin_run_upload(run=run, session_id=session.id, step_id="step", call_id="send",
                upload_source_key="message:file", filename="file.bin", media_type="application/octet-stream", byte_size=4, sha256=digest)
        blob = await files.begin_run_upload(run=run, session_id=session.id, step_id="step", call_id="send",
            upload_source_key="message:file", filename="file.bin", media_type="application/octet-stream", byte_size=4, sha256=digest, authorize=authorize)
        await files.publish_run_upload(run=run, session_id=session.id, attachment_id=blob.view.id,
            revision="revision", byte_size=4, sha256=digest, authorize=authorize)
        with pytest.raises(AccessDenied):
            await files.authorize_read(principal, session_id=session.id, attachment_id=blob.view.id)
        with pytest.raises(AccessDenied):
            await files.get_upload(principal, session_id=session.id, attachment_id=blob.view.id)
        message = await SessionService(tx).accept_external_message(run=run, session_id=session.id, step_id="step", call_id="send",
            input=InputContent("file result", (InputReference(blob.view.reference),)), authorize=authorize)
        bound, = await files.bind_to_message(run=run, session_id=session.id, message_id=message.entry.id, attachment_ids=(blob.view.id,))
        assert bound.created_by_run_id == run.id and bound.uploader_membership_id is None
        assert bound.bound_message_id == message.entry.id and bound.origin_input_id is None
        readable = await files.authorize_read(principal, session_id=session.id, attachment_id=blob.view.id)
        assert (await files.authorize_delivery(tenant_id=run.tenant_id, agent_id=run.agent_id,
            message_id=message.entry.id, attachment_id=blob.view.id)).view == bound
        future = datetime.now(UTC) + timedelta(days=2)
        assert await files.claim_cleanup(readable, now=future) is None
        assert not await files.finish_cleanup(readable, now=future)
        assert not await files.expired_unbound(now=future)


async def test_generated_file_uses_message_cutoff_not_borrowed_input_provenance(transaction_factory):
    principal, session = await setup(transaction_factory)
    initial = await accept(transaction_factory, principal, session)
    creator = await started(transaction_factory, principal, session, initial)
    early_input = await accept(transaction_factory, principal, session, "early", "before result")
    early = await started(transaction_factory, principal, session, early_input)
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=creator.tenant_id, run_id=creator.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("send", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "response", False)))
        files = SessionAttachmentService(tx)
        blob = await files.begin_run_upload(run=creator, session_id=session.id, step_id="step", call_id="send",
            upload_source_key="message:generated", filename="file", media_type="text/plain", byte_size=4, sha256=hashlib.sha256(b"data").hexdigest())
        await files.publish_run_upload(run=creator, session_id=session.id, attachment_id=blob.view.id,
            revision="revision", byte_size=4, sha256=blob.view.sha256)
        message = await SessionService(tx).accept_message(run=creator, step_id="step", call_id="send",
            input=InputContent("file", (InputReference(blob.view.reference),)))
        await files.bind_to_message(run=creator, session_id=session.id, message_id=message.entry.id, attachment_ids=(blob.view.id,))
        with pytest.raises(AccessDenied):
            await files.authorize_run_read(tenant_id=creator.tenant_id, run_id=early.id, attachment_id=blob.view.id)
        await RunService(tx).record_model_step(tenant_id=early.tenant_id, run_id=early.id,
            payload=ModelStepPayload("early-step", 1, ModelStepResult("", (ModelToolCall("early-send", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "early-response", False)))
        attempted = await SessionService(tx).accept_message(run=early, step_id="early-step", call_id="early-send",
            input=InputContent("opaque reference is not a grant", (InputReference(blob.view.reference),)))
        with pytest.raises(AccessDenied):
            await files.authorize_delivery(tenant_id=early.tenant_id, agent_id=early.agent_id,
                message_id=attempted.entry.id, attachment_id=blob.view.id)
        assert (await files.authorize_run_read(tenant_id=creator.tenant_id, run_id=creator.id, attachment_id=blob.view.id)).view.bound_message_id == message.entry.id
        later = await SessionService(tx).accept_input(principal, session_id=session.id, source_key="later",
            input=InputContent("reuse", (InputReference(blob.view.reference),)))
        rebound, = await files.bind_to_input(principal, session_id=session.id, input_id=later.entry.id, attachment_ids=(blob.view.id,))
        assert rebound.origin_input_id is None and rebound.bound_message_id == message.entry.id


@pytest.mark.parametrize("count,size,valid", [(8,2*1024*1024,True),(9,1,False),(5,4*1024*1024,False)])
async def test_message_attachment_count_and_aggregate_bounds(transaction_factory, count, size, valid):
    _, session, run = await scheduled(transaction_factory)
    async def authorize(tx, *, run, target_id, conversation_id, input):
        assert target_id == session.id
    async with transaction_factory() as tx:
        files = SessionAttachmentService(tx)
        blobs = []
        for index in range(count):
            blob = await files.begin_run_upload(run=run, session_id=session.id, step_id="step", call_id="send",
                upload_source_key=f"message:{index}", filename=f"{index}.bin", media_type="application/octet-stream",
                byte_size=size, sha256="a"*64, authorize=authorize)
            await files.publish_run_upload(run=run, session_id=session.id, attachment_id=blob.view.id,
                revision=f"revision-{index}", byte_size=size, sha256="a"*64, authorize=authorize)
            blobs.append(blob)
        message = await SessionService(tx).accept_external_message(run=run, session_id=session.id, step_id="step", call_id="send",
            input=InputContent("bounded files", tuple(InputReference(blob.view.reference) for blob in blobs)), authorize=authorize)
        if valid:
            assert len(await files.bind_to_message(run=run, session_id=session.id, message_id=message.entry.id,
                attachment_ids=tuple(blob.view.id for blob in blobs))) == count
        else:
            with pytest.raises(InvalidInput):
                await files.bind_to_message(run=run, session_id=session.id, message_id=message.entry.id,
                    attachment_ids=tuple(blob.view.id for blob in blobs))
