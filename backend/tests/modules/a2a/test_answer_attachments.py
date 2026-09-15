"""Additional answer files remain exact, source-authorized request grants in Run History."""

from uuid import uuid4

import pytest
from modules.a2a.test_service import accept, setup
from modules.run.test_lifecycle import snapshot, step

from app.infrastructure.errors import AccessDenied
from app.modules.a2a.public import A2AService
from app.modules.run.public import InputContent, InputReference, RunService, SourceIdentity, WaitingPayload


async def test_answer_file_authorization_and_request_scoped_history(transaction_factory):
    p, source, target_agent = await setup(transaction_factory)
    request = await accept(transaction_factory, p, source, target_agent)
    target_id = uuid4()
    async with transaction_factory() as tx:
        target = (await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target_agent, run_id=target_id,
            snapshot=snapshot(p.tenant_id, target_agent, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))).run
    boundary = await step(transaction_factory, p.tenant_id, target_id)
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=target_id,
            payload=WaitingPayload("step", "question", "Second file?", boundary), waiting_consumer=A2AService(tx))
    reference = "attachment:session:" + str(uuid4())
    content = InputContent("Additional file", (InputReference(reference),))
    async with transaction_factory() as tx:
        owner = A2AService(tx)
        with pytest.raises(AccessDenied):
            await owner.answer(tenant_id=p.tenant_id, source_run_id=source.id, request_id=request.id,
                step_id="step", call_id="call", waiting_reference="question", input=content)
        async def forbidden(transaction, *, run, reference):
            raise AccessDenied("Source cannot read the selected attachment")
        with pytest.raises(AccessDenied):
            await owner.answer(tenant_id=p.tenant_id, source_run_id=source.id, request_id=request.id,
                step_id="step", call_id="call", waiting_reference="question", input=content, attachment_authorizer=forbidden)
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=target_id)).status == "Waiting"
        observed = []
        async def authorized(transaction, *, run, reference):
            observed.append((run.id, reference))
        await owner.answer(tenant_id=p.tenant_id, source_run_id=source.id, request_id=request.id,
            step_id="step", call_id="call", waiting_reference="question", input=content, attachment_authorizer=authorized)
        assert observed == [(source.id, reference)]
        assert (await owner.get(tenant_id=p.tenant_id, request_id=request.id)).input.references == ()
        await owner.authorize_attachment_reference(tx, run=target, reference=reference)
        for kind, identity in (("a2a_answer", uuid4()), ("unrelated", request.id)):
            foreign = "attachment:session:" + str(uuid4())
            await RunService(tx).append_related(tenant_id=p.tenant_id, run_id=target_id,
                input=InputContent("Unrelated reference", (InputReference(foreign),)), source=SourceIdentity(kind, identity, str(uuid4())))
            with pytest.raises(AccessDenied):
                await owner.authorize_attachment_reference(tx, run=target, reference=foreign)
