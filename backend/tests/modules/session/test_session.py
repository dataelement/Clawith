"""Session owner and real Run transactional ports; no HTTP or hosted Model claim."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools
from sqlalchemy import select, update

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity, WaitingPayload
from app.modules.session.models import SessionEntryRecord
from app.modules.session.public import SessionConsumers, SessionService


async def setup(factory):
    async with factory() as tx:
        data = await _seed_to_agent(tx.session)
        principal = TenantPrincipal(data["account"].id, data["membership"].id, data["tenant"].id, "tenant_admin")
        session = await SessionService(tx).create(principal, agent_id=data["agent"].id)
    return principal, session


async def accept(factory, principal, session, key="input", text="Work"):
    async with factory() as tx:
        return await SessionService(tx).accept_input(principal, session_id=session.id, source_key=key, input=InputContent(text))


async def start(factory, principal, session, receipt):
    run_id = uuid4()
    async with factory() as tx:
        result = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=session.agent_id, run_id=run_id,
            snapshot=with_tools(snapshot(principal.tenant_id, session.agent_id, run_id), "send_message", "need_input"),
            input=receipt.entry.content, source=SourceIdentity("session", session.id, str(receipt.link.id)), start_consumer=SessionConsumers())
    return result.run


async def model_step(factory, run, *, step="step", call="message", name="send_message"):
    async with factory() as tx:
        service = RunService(tx)
        boundary = (await service.get(tenant_id=run.tenant_id, run_id=run.id)).latest_history_sequence
        await service.record_model_step(tenant_id=run.tenant_id, run_id=run.id,
            payload=ModelStepPayload(step, boundary, ModelStepResult("", (ModelToolCall(call, name, "{}"),), "tool_calls", ModelUsage(), step, False)))
    return boundary


async def test_inputs_are_immutable_deduplicated_and_cutoffs_fixed(transaction_factory):
    p, session = await setup(transaction_factory)
    first = await accept(transaction_factory, p, session)
    again = await accept(transaction_factory, p, session, text="Different retry")
    second = await accept(transaction_factory, p, session, "second", "new work")
    assert first.entry == again.entry and first.link == again.link and not again.created
    assert first.link.history_cutoff == 1 and second.link.history_cutoff == 2
    async with transaction_factory() as tx:
        page = await SessionService(tx).read_history(p, session_id=session.id, through_position=1)
        assert page.entries == (first.entry,) and not page.has_more


async def test_concurrent_inputs_get_unique_positions_and_same_source_one_accept(transaction_factory):
    p, session = await setup(transaction_factory)
    values = await asyncio.gather(*(accept(transaction_factory, p, session, str(i)) for i in range(8)))
    assert sorted(value.entry.position for value in values) == list(range(1, 9))
    retries = await asyncio.gather(*(accept(transaction_factory, p, session, "same") for _ in range(4)))
    assert sum(value.created for value in retries) == 1
    assert len({value.entry.id for value in retries}) == 1


async def test_membership_and_captured_agent_scope_protect_all_reads(transaction_factory):
    p, session = await setup(transaction_factory)
    await accept(transaction_factory, p, session)
    async with transaction_factory() as tx:
        service = SessionService(tx)
        assert (await service.list(replace(p, membership_id=uuid4()))).sessions == ()
        for wrong in (replace(p, membership_id=uuid4()), replace(p, role="member", allowed_agent_ids=frozenset())):
            with pytest.raises(AccessDenied):
                await service.read_history(wrong, session_id=session.id)
        with pytest.raises(NotFound):
            await service.get(replace(p, tenant_id=uuid4()), session_id=session.id)


async def test_start_association_and_message_acceptance_survive_terminal_without_final_reply(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await start(transaction_factory, p, session, receipt)
    await model_step(transaction_factory, run)
    async with transaction_factory() as tx:
        messages = SessionService(tx)
        first = await messages.accept_message(run=run, step_id="step", call_id="message", input=InputContent("Progress"))
        retry = await messages.accept_message(run=run, step_id="step", call_id="message", input=InputContent("Different retry"))
        assert first.created and not retry.created and retry.entry == first.entry
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=run.id, status="Cancelled", reason="stopped", consumer=SessionConsumers())
    async with transaction_factory() as tx:
        messages = SessionService(tx)
        retry = await messages.accept_message(run=run, step_id="step", call_id="message", input=InputContent("Retry after terminal"))
        assert retry.entry == first.entry and not retry.created
        link = await messages.get_link(p, session_id=session.id, link_id=receipt.link.id)
        assert link.result.status == "Cancelled" and link.result.run_id == run.id
        assert len((await messages.read_history(p, session_id=session.id)).entries) == 2
        delivered = await messages.get_message_for_delivery(tenant_id=p.tenant_id, agent_id=session.agent_id, message_id=first.entry.id)
        assert delivered.origin_input_id == receipt.entry.id and delivered.source_run_id == run.id
        with pytest.raises(NotFound):
            await messages.get_message_for_delivery(tenant_id=p.tenant_id, agent_id=uuid4(), message_id=first.entry.id)


async def test_waiting_question_and_run_transition_roll_back_together(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await start(transaction_factory, p, session, receipt)
    boundary = await model_step(transaction_factory, run, name="need_input")
    waiting = WaitingPayload("step", "waiting", "Which source?", boundary)
    class FailedConsumer(SessionConsumers):
        async def record_waiting(self, transaction, **kwargs):
            await super().record_waiting(transaction, **kwargs)
            raise RuntimeError("delivery index rollback")
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).wait(tenant_id=p.tenant_id, run_id=run.id, payload=waiting, waiting_consumer=FailedConsumer())
    async with transaction_factory() as tx:
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=run.id)).status == "Running"
        assert len((await SessionService(tx).read_history(p, session_id=session.id)).entries) == 1
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=run.id, payload=waiting, waiting_consumer=SessionConsumers())
    async with transaction_factory() as tx:
        reply = await SessionService(tx).accept_input(p, session_id=session.id, source_key="answer", input=InputContent("Source A"),
            reply_to_run_id=run.id, waiting_reference="waiting")
        assert reply.link is None and reply.entry.related_waiting_run_id == run.id


async def test_runtime_history_never_crosses_frozen_session_cutoff(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await start(transaction_factory, p, session, receipt)
    await accept(transaction_factory, p, session, "later", "Must not leak")
    async with transaction_factory() as tx:
        service = SessionService(tx)
        page = await service.read_execution_history(run)
        assert page.through_position == 1 and [item.content.text for item in page.entries] == ["Work"]
        with pytest.raises(InvalidInput):
            await service.read_execution_history(run, after_position=2)


async def test_context_tail_is_bounded_ordered_and_large_entry_explicitly_referenced(transaction_factory):
    p, session = await setup(transaction_factory)
    for i in range(4):
        await accept(transaction_factory, p, session, str(i), "x" * 5000 if i == 3 else str(i))
    async with transaction_factory() as tx:
        page = await SessionService(tx).read_context_history(p, session_id=session.id, through_position=4, limit=2, max_bytes=1024)
        assert [entry.position for entry in page.entries] == [3, 4]
        assert page.entries[-1].reference_only and page.entries[-1].content is None and page.has_more


async def test_versions_bounds_and_input_rollback(transaction_factory):
    p, session = await setup(transaction_factory)
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await SessionService(tx).accept_input(p, session_id=session.id, source_key="rolled", input=InputContent("work"))
            raise RuntimeError("rollback")
    receipt = await accept(transaction_factory, p, session)
    assert receipt.entry.position == 1
    async with transaction_factory() as tx:
        service = SessionService(tx)
        with pytest.raises(InvalidInput):
            await service.accept_input(p, session_id=session.id, source_key="x", input=InputContent("中" * 100000))
        await tx.session.execute(update(SessionEntryRecord).where(SessionEntryRecord.id == receipt.entry.id).values(payload_version=2))
        with pytest.raises(InvalidInput):
            await service.read_history(p, session_id=session.id)
        assert await tx.session.scalar(select(SessionEntryRecord.position)) == 1


async def test_two_messages_in_one_model_step_remain_distinct_and_final_does_not_send(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await start(transaction_factory, p, session, receipt)
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", tuple(ModelToolCall(key, "send_message", "{}") for key in ("a", "b")),
                "tool_calls", ModelUsage(), "step", False)))
        service = SessionService(tx)
        one = await service.accept_message(run=run, step_id="step", call_id="a", input=InputContent("first"))
        two = await service.accept_message(run=run, step_id="step", call_id="b", input=InputContent("second"))
        assert (one.entry.position, two.entry.position) == (2, 3)
        assert one.entry.message_key != two.entry.message_key


async def test_start_consumer_failure_rolls_back_run_and_link_association(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run_id = uuid4()
    class Reject(SessionConsumers):
        async def record_started(self, tx, *, run):
            await super().record_started(tx, run=run)
            raise RuntimeError("association failure")
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).start(tenant_id=p.tenant_id, agent_id=session.agent_id, run_id=run_id,
                snapshot=snapshot(p.tenant_id, session.agent_id, run_id), input=receipt.entry.content,
                source=SourceIdentity("session", session.id, str(receipt.link.id)), start_consumer=Reject())
    async with transaction_factory() as tx:
        assert (await SessionService(tx).get_link(p, session_id=session.id, link_id=receipt.link.id)).admission == "pending"
        with pytest.raises(NotFound):
            await RunService(tx).get(tenant_id=p.tenant_id, run_id=run_id)


async def test_unseen_input_suppresses_waiting_question(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await start(transaction_factory, p, session, receipt)
    boundary = await model_step(transaction_factory, run, name="need_input")
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.append_related(tenant_id=p.tenant_id, run_id=run.id, input=InputContent("already answered"),
            source=SourceIdentity("fixture", session.id, "extra"))
        waited = await service.wait(tenant_id=p.tenant_id, run_id=run.id, payload=WaitingPayload("step", "wait", "Question?", boundary),
            waiting_consumer=SessionConsumers())
        assert not waited.changed
        assert len((await SessionService(tx).read_history(p, session_id=session.id)).entries) == 1


async def test_same_session_work_authority_rejects_other_session_and_forged_source(transaction_factory):
    p, a = await setup(transaction_factory)
    async with transaction_factory() as tx:
        b = await SessionService(tx).create(p, agent_id=a.agent_id)
    run_a = await start(transaction_factory, p, a, await accept(transaction_factory, p, a))
    run_b = await start(transaction_factory, p, b, await accept(transaction_factory, p, b))
    async with transaction_factory() as tx:
        service = SessionService(tx)
        with pytest.raises(AccessDenied):
            await service.authorize_work(run=run_a, target_run_id=run_b.id)
        context = await service.get_execution_context(replace(run_a, source=SourceIdentity("session", b.id, "forged")))
        assert context.session.id == a.id


async def test_large_history_entry_can_be_read_in_bounded_fragments(transaction_factory):
    import json
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session, text="中" * 40000)
    run = await start(transaction_factory, p, session, receipt)
    parts = []
    offset = 0
    async with transaction_factory() as tx:
        service = SessionService(tx)
        while True:
            fragment = await service.read_execution_history_fragment(run, content_offset=offset, max_characters=7000)
            assert len(fragment.content_json) <= 7000
            assert fragment.through_position == 1 and fragment.position == 1
            parts.append(fragment.content_json)
            if fragment.next_offset is None:
                assert fragment.next_after_position == 1
                break
            offset = fragment.next_offset
        assert json.loads("".join(parts))["text"] == "中" * 40000
        assert await service.read_execution_history_fragment(run, after_position=1) is None


async def test_waiting_reply_locks_run_before_session_against_terminal_consumer(transaction_factory, monkeypatch):
    p, session = await setup(transaction_factory)
    run = await start(transaction_factory, p, session, await accept(transaction_factory, p, session))
    boundary = await model_step(transaction_factory, run, name="need_input")
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=run.id,
            payload=WaitingPayload("step", "wait", "Question?", boundary), waiting_consumer=SessionConsumers())
    held = asyncio.Event()
    release = asyncio.Event()
    reply_requested_run = asyncio.Event()
    original = RunService.lock_main
    async def lock_main(self, **kwargs):
        if asyncio.current_task().get_name() == "session-reply-regression":
            reply_requested_run.set()
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "lock_main", lock_main)
    async def terminate():
        async with transaction_factory() as tx:
            service = RunService(tx)
            await service.lock_main(tenant_id=p.tenant_id, run_id=run.id)
            held.set()
            await release.wait()
            await service.terminate(tenant_id=p.tenant_id, run_id=run.id, status="Cancelled", reason="race", consumer=SessionConsumers())
    async def reply():
        await held.wait()
        async with transaction_factory() as tx:
            return await SessionService(tx).accept_input(p, session_id=session.id, source_key="answer",
                input=InputContent("answer"), reply_to_run_id=run.id, waiting_reference="wait")
    terminal = asyncio.create_task(terminate())
    responder = asyncio.create_task(reply(), name="session-reply-regression")
    try:
        await asyncio.wait_for(reply_requested_run.wait(), 2)
        # The reply is waiting on Run and must not prevent an independent Session append.
        await asyncio.wait_for(accept(transaction_factory, p, session, "independent"), 2)
        release.set()
        await asyncio.wait_for(terminal, 2)
        with pytest.raises(Conflict):
            await asyncio.wait_for(responder, 2)
    finally:
        release.set()
        await asyncio.gather(terminal, responder, return_exceptions=True)


async def test_accepted_wait_reply_can_be_retried_after_run_resumes(transaction_factory):
    p, session = await setup(transaction_factory)
    run = await start(transaction_factory, p, session, await accept(transaction_factory, p, session))
    boundary = await model_step(transaction_factory, run, name="need_input")
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=run.id,
            payload=WaitingPayload("step", "wait", "Question?", boundary), waiting_consumer=SessionConsumers())
    async with transaction_factory() as tx:
        receipt = await SessionService(tx).accept_input(p, session_id=session.id, source_key="answer", input=InputContent("yes"),
            reply_to_run_id=run.id, waiting_reference="wait")
    async with transaction_factory() as tx:
        await RunService(tx).append_related(tenant_id=p.tenant_id, run_id=run.id, input=receipt.entry.content,
            source=SourceIdentity("session_input", receipt.entry.id, "waiting_reply"), waiting_reference="wait")
    async with transaction_factory() as tx:
        repeated = await SessionService(tx).accept_input(p, session_id=session.id, source_key="answer", input=InputContent("changed"),
            reply_to_run_id=run.id, waiting_reference="wait")
        assert not repeated.created and repeated.entry == receipt.entry
