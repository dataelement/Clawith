"""Trusted message uploads require real Main Tool facts and atomic message binding."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from modules.group.test_service import setup
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput
from app.modules.group.public import GroupAttachmentService, GroupService
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, InputReference, ModelStepPayload, RunService, SourceIdentity
from app.modules.session.public import SessionAttachmentService, SessionConsumers, SessionService


@pytest.mark.parametrize("kind", ["session", "group"])
async def test_message_upload_requires_tool_source_and_retains_only_after_acceptance(transaction_factory, kind):
    principal, _, group, agent = await setup(transaction_factory)
    run_id = uuid4()
    owner_type = SessionAttachmentService if kind == "session" else GroupAttachmentService
    async with transaction_factory() as tx:
        if kind == "session":
            product = await SessionService(tx).create(principal, agent_id=agent)
            accepted = await SessionService(tx).accept_input(principal, session_id=product.id, source_key="input", input=InputContent("Work"))
            source = SourceIdentity("session", product.id, str(accepted.link.id))
            consumer = SessionConsumers()
            destination = {"session_id": product.id}
            binding_destination = destination
        else:
            accepted = await GroupService(tx).accept_input(principal, group_id=group.id, source_key="input",
                input=InputContent("Work"), agent_ids=(agent,))
            source = SourceIdentity("group", accepted.event.id, str(agent))
            consumer = GroupService(tx)
            destination = {"group_id": group.id, "conversation_id": accepted.event.conversation_id}
            binding_destination = {"group_id": group.id}
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id,
            snapshot=with_tools(snapshot(principal.tenant_id, agent, run_id), "send_message"), source=source,
            input=InputContent("Work"), start_consumer=consumer)).run
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("send", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
    def upload_source(ordinal):
        return "message:" + sha256(f"{run.id}\0step\0send\0{ordinal}".encode()).hexdigest()
    publication = {"run": run, **destination}
    binding = {"run": run, **binding_destination}
    args = {**publication, "step_id": "step", "call_id": "send", "upload_source_key": upload_source(0)}
    content = {"filename": "file.bin", "media_type": "application/octet-stream", "byte_size": 4, "sha256": sha256(b"data").hexdigest()}
    async with transaction_factory() as tx:
        owner = owner_type(tx)
        with pytest.raises(InvalidInput):
            await owner.begin_run_upload(**{**args, "call_id": "invented"}, **content)
        blob = await owner.begin_run_upload(**args, **content)
        await owner.publish_run_upload(**publication, attachment_id=blob.view.id, revision="rev", byte_size=4, sha256=content["sha256"])
        with pytest.raises(AccessDenied):
            await owner.bind_to_message(**binding,
                message_id=uuid4(), attachment_ids=(blob.view.id,))
    message_input = InputContent("File", (InputReference(blob.view.reference),))
    early_id = uuid4()
    async with transaction_factory() as tx:
        if kind == "session":
            later = await SessionService(tx).accept_input(principal, session_id=product.id, source_key="parallel", input=InputContent("Parallel"))
            later_source, later_consumer = SourceIdentity("session", product.id, str(later.link.id)), SessionConsumers()
        else:
            later = await GroupService(tx).accept_input(principal, group_id=group.id, source_key="parallel", input=InputContent("Parallel"), agent_ids=(agent,))
            later_source, later_consumer = SourceIdentity("group", later.event.id, str(agent)), GroupService(tx)
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=early_id,
            snapshot=snapshot(principal.tenant_id, agent, early_id), source=later_source,
            input=InputContent("Parallel"), start_consumer=later_consumer)
    async with transaction_factory() as tx:
        if kind == "session":
            message_id = (await SessionService(tx).accept_message(run=run, step_id="step", call_id="send", input=message_input)).entry.id
        else:
            message_id = (await GroupService(tx).accept_message(tenant_id=principal.tenant_id,
                run_id=run_id, step_id="step", call_id="send", input=message_input)).id
        await owner_type(tx).bind_to_message(**binding,
            message_id=message_id, attachment_ids=(blob.view.id,))
    async with transaction_factory() as tx:
        owner = owner_type(tx)
        assert (await owner.authorize_run_read(tenant_id=principal.tenant_id, run_id=run_id,
            attachment_id=blob.view.id)).view.id == blob.view.id
        with pytest.raises(AccessDenied, match="fixed input cutoff"):
            await owner.authorize_run_read(tenant_id=principal.tenant_id, run_id=early_id, attachment_id=blob.view.id)
        await RunService(tx).append_related(tenant_id=principal.tenant_id, run_id=early_id, input=message_input,
            source=SourceIdentity("explicit_file", uuid4(), "file"))
        assert (await owner.authorize_run_read(tenant_id=principal.tenant_id, run_id=early_id,
            attachment_id=blob.view.id)).view.id == blob.view.id
        assert (await owner.authorize_delivery(tenant_id=principal.tenant_id, agent_id=agent,
            message_id=message_id, attachment_id=blob.view.id)).storage_revision == "rev"
        assert await owner.expired_unbound(now=datetime.now(UTC) + timedelta(hours=25)) == ()
        orphan = await owner.begin_run_upload(**{**args, "upload_source_key": upload_source(1)}, **content)
    async with transaction_factory() as tx:
        assert await owner_type(tx).claim_cleanup(orphan, now=datetime.now(UTC) + timedelta(hours=25)) is not None
    async with transaction_factory() as tx:
        with pytest.raises(Conflict):
            await owner_type(tx).publish_run_upload(**publication, attachment_id=orphan.view.id,
                revision="late", byte_size=4, sha256=content["sha256"])
