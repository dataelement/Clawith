"""Group services use real persistence and Run callbacks below product transport."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from modules.run.test_lifecycle import snapshot, step
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.modules.agent.models import AgentRecord
from app.modules.group.models import GroupEventRecord
from app.modules.group.public import GroupService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity, WaitingPayload


async def setup(transaction_factory):
    async with transaction_factory() as tx:
        data = await _seed_to_agent(tx.session)
        tenant, agent = data["tenant"].id, data["agent"].id
        creator = TenantPrincipal(data["account"].id, data["membership"].id, tenant, "member", frozenset({agent}))
        identity = IdentityService(tx)
        account = await identity.create_account()
        member = await identity.create_membership(tenant_id=tenant, account_id=account.id, display_name="Peer", role="member")
        peer = TenantPrincipal(account.id, member.id, tenant, "member", frozenset({agent}))
        group = await GroupService(tx).create(creator, name="Team")
        await GroupService(tx).set_agent(creator, group_id=group.id, agent_id=agent, enabled=True)
    return creator, peer, group, agent


async def accepted(transaction_factory, creator, group, agent):
    async with transaction_factory() as tx:
        return await GroupService(tx).accept_input(creator, group_id=group.id, source_key="human-1",
            input=InputContent("Research report"), agent_ids=(agent,))


async def started(transaction_factory, creator, group, agent):
    event = await accepted(transaction_factory, creator, group, agent)
    run = uuid4()
    async with transaction_factory() as tx:
        result = await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=agent, run_id=run,
            snapshot=with_tools(snapshot(creator.tenant_id, agent, run), "send_message"), input=event.event.input,
            source=SourceIdentity("group", event.event.id, str(agent)), start_consumer=GroupService(tx))
        await RunService(tx).record_model_step(tenant_id=creator.tenant_id, run_id=run,
            payload=ModelStepPayload("message-step", 1, ModelStepResult("", (
                ModelToolCall("send", "send_message", "{}"),), "tool_calls", ModelUsage(), "message-step", False)))
    return event, result.run


async def test_ordinary_member_can_create_invite_and_edit_but_outsider_cannot_read(transaction_factory):
    creator, peer, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        with pytest.raises(AccessDenied):
            await service.get(peer, group_id=group.id)
        await service.set_membership(creator, group_id=group.id, membership_id=peer.membership_id, enabled=True)
        assert (await service.update(peer, group_id=group.id, name="Edited", announcement="Shared guide", enabled=True)).name == "Edited"
        with pytest.raises(AccessDenied):
            await service.set_membership(peer, group_id=group.id, membership_id=creator.membership_id, enabled=False)
        await service.set_membership(creator, group_id=group.id, membership_id=peer.membership_id, enabled=False)
        with pytest.raises(AccessDenied):
            await service.accept_input(peer, group_id=group.id, source_key="outsider", input=InputContent("x"), agent_ids=(agent,))
        with pytest.raises(Conflict):
            await service.set_membership(creator, group_id=group.id, membership_id=creator.membership_id, enabled=False)


async def test_input_concurrency_deduplication_and_fixed_history_cutoff(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    one, two = await asyncio.gather(*(accepted(transaction_factory, creator, group, agent) for _ in range(2)))
    assert one.event.id == two.event.id and one.created != two.created
    async with transaction_factory() as tx:
        service = GroupService(tx)
        next_event = await service.accept_input(creator, group_id=group.id, source_key="human-2", input=InputContent("Other"), agent_ids=())
        assert next_event.event.position == 2
        history = await service.list_events(creator, group_id=group.id, through_position=1)
        assert len(history) == 1 and history[0].id == one.event.id
        assert len(await service.links(creator, group_id=group.id, event_id=one.event.id)) == 1
        with pytest.raises(InvalidInput):
            await service.list_events(creator, group_id=group.id, limit=101)


async def test_message_commit_survives_missing_tool_result_and_interruption_without_extra_reply(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    event, run = await started(transaction_factory, creator, group, agent)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        message = await service.accept_message(tenant_id=creator.tenant_id, run_id=run.id,
            step_id="message-step", call_id="send", input=InputContent("Accepted work"))
        duplicate = await service.accept_message(tenant_id=creator.tenant_id, run_id=run.id,
            step_id="message-step", call_id="send", input=InputContent("Changed"))
        assert message.id == duplicate.id and duplicate.input.text == "Accepted work"
        assert (message.step_id, message.call_id) == ("message-step", "send")
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=creator.tenant_id, run_id=run.id, status="Interrupted",
            reason="service_interruption", consumer=GroupService(tx))
    async with transaction_factory() as tx:
        service = GroupService(tx)
        assert len(await service.list_events(creator, group_id=group.id)) == 2
        assert (await service.links(creator, group_id=group.id, event_id=event.event.id))[0].result["status"] == "Interrupted"
        assert (await service.get_message_for_delivery(tenant_id=creator.tenant_id, agent_id=agent, message_id=message.id)).id == message.id
        with pytest.raises(NotFound):
            await service.get_message_for_delivery(tenant_id=creator.tenant_id, agent_id=uuid4(), message_id=message.id)


async def test_wait_atomic_rollback_and_explicit_member_answer(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, creator, group, agent)
    boundary = await step(transaction_factory, creator.tenant_id, run.id)
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).wait(tenant_id=creator.tenant_id, run_id=run.id,
                payload=WaitingPayload("step", "wait", "Which date?", boundary), waiting_consumer=GroupService(tx))
            raise RuntimeError("rollback")
    async with transaction_factory() as tx:
        assert len(await GroupService(tx).list_events(creator, group_id=group.id)) == 1
        assert (await RunService(tx).get(tenant_id=creator.tenant_id, run_id=run.id)).status == "Running"
        await RunService(tx).wait(tenant_id=creator.tenant_id, run_id=run.id,
            payload=WaitingPayload("step", "wait", "Which date?", boundary), waiting_consumer=GroupService(tx))
    async with transaction_factory() as tx:
        service = GroupService(tx)
        answer, changed = await service.answer_wait(creator, group_id=group.id, run_id=run.id,
            waiting_reference="wait", source_key="answer", input=InputContent("Tomorrow"))
        assert changed.run.status == "Running" and answer.event.related_run_id == run.id
    async with transaction_factory() as tx:
        repeated, changed = await GroupService(tx).answer_wait(creator, group_id=group.id, run_id=run.id,
            waiting_reference="wait", source_key="answer", input=InputContent("Different"))
        assert not changed.changed and repeated.event.input.text == "Tomorrow"


async def test_group_and_agent_scope_fail_before_event_acceptance(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        with pytest.raises(NotFound):
            await service.get(replace(creator, tenant_id=uuid4()), group_id=group.id)
        with pytest.raises(AccessDenied):
            await service.accept_input(replace(creator, allowed_agent_ids=frozenset()), group_id=group.id,
                source_key="denied", input=InputContent("x"), agent_ids=(agent,))
        with pytest.raises(InvalidInput):
            await service.accept_input(creator, group_id=group.id, source_key="big", input=InputContent("中" * 100000), agent_ids=(agent,))
        assert not await service.list_events(creator, group_id=group.id)


async def test_one_target_admission_failure_does_not_erase_other_target_or_event(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        first = await tx.session.get(AgentRecord, agent)
        second = AgentRecord(id=uuid4(), tenant_id=creator.tenant_id, model_id=first.model_id,
            created_by_membership_id=creator.membership_id, name="Second", soul="Independent", timezone="UTC", enabled=True,
            created_at=first.created_at, updated_at=first.updated_at)
        tx.session.add(second)
        await tx.session.flush()
        second_id = second.id
    creator = replace(creator, allowed_agent_ids=frozenset({agent, second_id}))
    async with transaction_factory() as tx:
        await GroupService(tx).set_agent(creator, group_id=group.id, agent_id=second_id, enabled=True)
        accepted_input = await GroupService(tx).accept_input(creator, group_id=group.id, source_key="two-targets",
            input=InputContent("Compare results"), agent_ids=(agent, second_id))
    run_id = uuid4()
    async with transaction_factory() as tx:
        service = GroupService(tx)
        await service.mark_admission_failed(tenant_id=creator.tenant_id, event_id=accepted_input.event.id, agent_id=agent, reason="capacity")
        await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=second_id, run_id=run_id,
            snapshot=snapshot(creator.tenant_id, second_id, run_id), input=accepted_input.event.input,
            source=SourceIdentity("group", accepted_input.event.id, str(second_id)), start_consumer=service)
    async with transaction_factory() as tx:
        links = await GroupService(tx).links(creator, group_id=group.id, event_id=accepted_input.event.id)
        assert {link.agent_id: link.admission for link in links} == {agent: "failed", second_id: "started"}
        assert len(await GroupService(tx).list_events(creator, group_id=group.id)) == 1
        row = await tx.session.get(GroupEventRecord, accepted_input.event.id)
        row.payload_version = 2
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await GroupService(tx).list_events(creator, group_id=group.id)


async def test_history_pages_bound_materialized_payload_bytes(transaction_factory):
    creator, _, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        for index in range(6):
            await GroupService(tx).accept_input(creator, group_id=group.id, source_key=f"large-{index}",
                input=InputContent("x" * 250000), agent_ids=())
    async with transaction_factory() as tx:
        page = await GroupService(tx).list_events(creator, group_id=group.id)
        assert len(page) == 4
        rest = await GroupService(tx).list_events(creator, group_id=group.id, after_position=page[-1].position)
        assert len(rest) == 2
