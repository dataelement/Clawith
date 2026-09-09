"""Explicit A2A takeover preserves source attribution and independent target execution."""

from dataclasses import replace
from uuid import uuid4

import pytest
from modules.a2a.test_service import setup
from modules.run.test_lifecycle import snapshot, step
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput
from app.modules.a2a.public import A2AService
from app.modules.group.public import GroupService
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity, WaitingPayload
from app.modules.session.public import SessionConsumers, SessionService
from app.modules.workspace.public import WorkspaceSubject


async def session_main(factory, principal, agent_id, session_id=None):
    run_id = uuid4()
    async with factory() as tx:
        owner = SessionService(tx)
        if session_id is None:
            session_id = (await owner.create(principal, agent_id=agent_id)).id
        accepted = await owner.accept_input(principal, session_id=session_id, source_key=str(run_id), input=InputContent("Work"))
        captured = with_tools(snapshot(principal.tenant_id, agent_id, run_id), "send_message_to_agent")
        captured = replace(captured, workspace=replace(captured.workspace,
            output=WorkspaceSubject("membership", principal.membership_id), allow_shared_memory_writes=False))
        result = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent_id, run_id=run_id,
            snapshot=captured, input=accepted.entry.content,
            source=SourceIdentity("session", session_id, str(accepted.link.id)), start_consumer=SessionConsumers())
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
    return result.run, session_id


async def test_same_session_takeover_requires_previous_recipient_terminal_and_routes_result(transaction_factory):
    p, seeded, target = await setup(transaction_factory)
    original, session_id = await session_main(transaction_factory, p, seeded.agent_id)
    current, _ = await session_main(transaction_factory, p, seeded.agent_id, session_id)
    other, _ = await session_main(transaction_factory, p, seeded.agent_id)
    async with transaction_factory() as tx:
        request = await A2AService(tx).accept(tenant_id=p.tenant_id, source_run_id=original.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Research"))
        assert await A2AService(tx).read_result(tenant_id=p.tenant_id, source_run_id=current.id, request_id=request.id) is None
        with pytest.raises(AccessDenied):
            await A2AService(tx).read_result(tenant_id=p.tenant_id, source_run_id=other.id, request_id=request.id)
        with pytest.raises(Conflict):
            await A2AService(tx).takeover(tenant_id=p.tenant_id, source_run_id=current.id, request_id=request.id,
                step_id="step", call_id="call")
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=original.id, status="Cancelled", reason="finished",
            consumer=SessionConsumers())
        taken, waiting, changed = await A2AService(tx).prepare_wait(tenant_id=p.tenant_id, source_run_id=current.id,
            request_id=request.id, step_id="step", call_id="call")
        assert taken.source_run_id == original.id and taken.delivery_run_id == current.id and waiting and changed is None
    target_run_id = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target, run_id=target_run_id,
            snapshot=snapshot(p.tenant_id, target, target_run_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
    await step(transaction_factory, p.tenant_id, target_run_id)
    async with transaction_factory() as tx:
        await RunService(tx).complete(tenant_id=p.tenant_id, run_id=target_run_id, step_id="step", output="Returned research",
            consumer=A2AService(tx))
    async with transaction_factory() as tx:
        service = A2AService(tx)
        assert not await service.mark_delivery(tenant_id=p.tenant_id, request_id=request.id,
            delivery_key="terminal", source_terminal=True, recipient_run_id=original.id)
        assert (await service.get(tenant_id=p.tenant_id, request_id=request.id)).source_delivery == "pending"
        delivered = await service.deliver_pending(tenant_id=p.tenant_id, request_id=request.id)
        assert delivered.run.id == current.id
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=original.id)).status == "Cancelled"
        _, wait, _ = await service.prepare_wait(tenant_id=p.tenant_id, source_run_id=current.id, request_id=request.id,
            step_id="step", call_id="call")
        assert not wait


async def test_notify_cannot_wait_for_completed_work(transaction_factory):
    p, source, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = A2AService(tx)
        request = await service.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
            target_agent_id=target, intent="notify", input=InputContent("FYI"))
        with pytest.raises(InvalidInput):
            await service.prepare_wait(tenant_id=p.tenant_id, source_run_id=source.id, request_id=request.id,
                step_id="step", call_id="call")


async def group_main(factory, principal, agent_id, group_id, conversation_id):
    run_id = uuid4()
    async with factory() as tx:
        owner = GroupService(tx)
        accepted = await owner.accept_input(principal, group_id=group_id, conversation_id=conversation_id,
            source_key=str(run_id), input=InputContent("Group work"), agent_ids=(agent_id,))
        captured = with_tools(snapshot(principal.tenant_id, agent_id, run_id), "send_message_to_agent")
        captured = replace(captured, workspace=replace(captured.workspace,
            output=WorkspaceSubject("group", group_id), allow_shared_memory_writes=False))
        result = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent_id, run_id=run_id,
            snapshot=captured, input=accepted.event.input,
            source=SourceIdentity("group", accepted.event.id, str(agent_id)), start_consumer=owner)
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
    return result.run


async def test_group_takeover_scope_is_same_agent_group_and_topic(transaction_factory):
    p, seed, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        owner = GroupService(tx)
        group = await owner.create(p, name="Research")
        await owner.set_agent(p, group_id=group.id, agent_id=seed.agent_id, enabled=True)
        topic = await owner.resolve_conversation(p, group_id=group.id)
        other = await owner.create_conversation(p, group_id=group.id, title="Private thread")
    original = await group_main(transaction_factory, p, seed.agent_id, group.id, topic)
    current = await group_main(transaction_factory, p, seed.agent_id, group.id, topic)
    different = await group_main(transaction_factory, p, seed.agent_id, group.id, other.id)
    async with transaction_factory() as tx:
        service = A2AService(tx)
        request = await service.accept(tenant_id=p.tenant_id, source_run_id=original.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Analyze"))
        assert await service.read_result(tenant_id=p.tenant_id, source_run_id=current.id, request_id=request.id) is None
        with pytest.raises(AccessDenied):
            await service.read_result(tenant_id=p.tenant_id, source_run_id=different.id, request_id=request.id)
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=original.id, status="Cancelled", reason="done",
            consumer=GroupService(tx))
        claimed = await service.takeover(tenant_id=p.tenant_id, source_run_id=current.id, request_id=request.id,
            step_id="step", call_id="call")
        assert claimed.delivery_run_id == current.id


async def test_new_main_answers_existing_waiting_target_and_waits_for_next_result(transaction_factory):
    p, seed, target = await setup(transaction_factory)
    original, session_id = await session_main(transaction_factory, p, seed.agent_id)
    current, _ = await session_main(transaction_factory, p, seed.agent_id, session_id)
    target_id = uuid4()
    async with transaction_factory() as tx:
        service = A2AService(tx)
        request = await service.accept(tenant_id=p.tenant_id, source_run_id=original.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Research"))
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target, run_id=target_id,
            snapshot=snapshot(p.tenant_id, target, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=service)
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=original.id, status="Cancelled", reason="source ended",
            consumer=SessionConsumers())
    boundary = await step(transaction_factory, p.tenant_id, target_id)
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=target_id,
            payload=WaitingPayload("step", "question", "Which source?", boundary), waiting_consumer=A2AService(tx))
    async with transaction_factory() as tx:
        service = A2AService(tx)
        assert await service.deliver_pending(tenant_id=p.tenant_id, request_id=request.id) is None
        changed = await service.answer(tenant_id=p.tenant_id, request_id=request.id, source_run_id=current.id,
            step_id="step", call_id="call", waiting_reference="question", input=InputContent("Use public records"))
        assert changed.run.id == target_id and changed.run.status == "Running"
        state, waiting, _ = await service.prepare_wait(tenant_id=p.tenant_id, request_id=request.id,
            source_run_id=current.id, step_id="step", call_id="call")
        assert state.delivery_run_id == current.id and state.result is None and waiting
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=original.id)).status == "Cancelled"
