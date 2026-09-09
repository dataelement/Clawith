"""A2A service transactions retain independent target execution and delivery."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from modules.run.test_lifecycle import snapshot, start, step
from runtime.test_engine import with_tools
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.a2a.models import A2ARequestRecord
from app.modules.a2a.public import A2AService
from app.modules.agent.models import AgentRecord
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.permission.public import PermissionService
from app.modules.run.public import (
    InputContent,
    ModelStepPayload,
    RelatedInputPayload,
    RunService,
    SourceIdentity,
    WaitingPayload,
)


async def setup(transaction_factory):
    async with transaction_factory() as tx:
        seeded = await _seed_to_agent(tx.session)
        tenant, source = seeded["tenant"].id, seeded["agent"].id
        principal = TenantPrincipal(seeded["account"].id, seeded["membership"].id, tenant, "tenant_admin")
        target = AgentRecord(id=uuid4(), tenant_id=tenant, model_id=seeded["model"].id,
            created_by_membership_id=principal.membership_id, name="Target", soul="Own context", timezone="UTC",
            enabled=True, created_at=seeded["now"], updated_at=seeded["now"])
        tx.session.add(target)
        await tx.session.flush()
        await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
        target_id = target.id
    run_id = uuid4()
    captured = with_tools(snapshot(tenant, source, run_id), "send_message_to_agent")
    run = (await start(transaction_factory, tenant, source, run=run_id, snap=captured)).run
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=tenant, run_id=run.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (
                ModelToolCall("call", "send_message_to_agent", "{}"),), "tool_calls", ModelUsage(), "step", False)))
    return principal, run, target_id


async def accept(transaction_factory, principal, run, target, intent="consult"):
    async with transaction_factory() as tx:
        return await A2AService(tx).accept(tenant_id=principal.tenant_id, source_run_id=run.id,
            step_id="step", call_id="call", target_agent_id=target, intent=intent, input=InputContent("Research this"))


@pytest.mark.parametrize("intent", ["notify", "consult", "task_delegate"])
async def test_accept_deduplicates_each_intent_without_starting_or_inheriting(transaction_factory, intent):
    principal, source, target = await setup(transaction_factory)
    one, two = await asyncio.gather(*(accept(transaction_factory, principal, source, target, intent) for _ in range(2)))
    assert one.id == two.id and one.admission == "pending" and one.target_run_id is None
    assert one.source_delivery == ("not_required" if intent == "notify" else "awaiting_result")
    async with transaction_factory() as tx:
        row = await tx.session.get(A2ARequestRecord, one.id)
        assert row.delegated_connections == []
        assert row.payload["step_id"] == "step" and row.payload["call_id"] == "call"


async def test_target_outcome_survives_source_termination_and_delivers_once(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    target_id = uuid4()
    async with transaction_factory() as tx:
        started = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=target, run_id=target_id,
            snapshot=snapshot(principal.tenant_id, target, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
        assert started.run.parent_run_id is None
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=principal.tenant_id, run_id=source.id, status="Cancelled", reason="user")
        assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=target_id)).status == "Running"
    await step(transaction_factory, principal.tenant_id, target_id)
    async with transaction_factory() as tx:
        await RunService(tx).complete(tenant_id=principal.tenant_id, run_id=target_id,
            step_id="step", output="Research result", consumer=A2AService(tx))
    async with transaction_factory() as tx:
        service = A2AService(tx)
        pending = await service.pending_deliveries(tenant_id=principal.tenant_id)
        assert len(pending) == 1 and pending[0].result["text"] == "Research result"
        detail = await service.read_result(tenant_id=principal.tenant_id, source_run_id=source.id, request_id=request.id)
        assert detail.kind == "terminal_outcome" and "Research result" in detail.content_json_fragment
        assert not await service.mark_delivery(tenant_id=principal.tenant_id, request_id=request.id, delivery_key="old", source_terminal=True)
        assert await service.mark_delivery(tenant_id=principal.tenant_id, request_id=request.id, delivery_key="terminal", source_terminal=True)
        assert not await service.mark_delivery(tenant_id=principal.tenant_id, request_id=request.id, delivery_key="terminal", source_terminal=True)
        assert (await service.get(tenant_id=principal.tenant_id, request_id=request.id)).source_delivery == "source_terminal"


async def test_wait_question_rolls_back_and_old_ack_cannot_hide_new_outcome(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    target_id = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=target, run_id=target_id,
            snapshot=snapshot(principal.tenant_id, target, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
    boundary = await step(transaction_factory, principal.tenant_id, target_id)
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).wait(tenant_id=principal.tenant_id, run_id=target_id,
                payload=WaitingPayload("step", "wait", "Which source?", boundary), waiting_consumer=A2AService(tx))
            raise RuntimeError("rollback")
    async with transaction_factory() as tx:
        assert (await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request.id)).result is None
        assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=target_id)).status == "Running"
        await RunService(tx).wait(tenant_id=principal.tenant_id, run_id=target_id,
            payload=WaitingPayload("step", "wait", "Which source?", boundary), waiting_consumer=A2AService(tx))
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=principal.tenant_id, run_id=target_id, status="Interrupted",
            reason="service_interruption", consumer=A2AService(tx))
        assert not await A2AService(tx).mark_delivery(tenant_id=principal.tenant_id, request_id=request.id,
            delivery_key="waiting:wait", source_terminal=False)
        assert (await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request.id)).source_delivery == "pending"


async def test_scope_visibility_actual_call_and_payload_denials(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        await PermissionService(tx).set_visibility(principal, agent_id=target, visibility="restricted")
    with pytest.raises(AccessDenied):
        await accept(transaction_factory, principal, source, target)
    with pytest.raises(NotFound):
        await accept(transaction_factory, replace(principal, tenant_id=uuid4()), source, target)
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await A2AService(tx).accept(tenant_id=principal.tenant_id, source_run_id=source.id,
                step_id="step", call_id="forged", target_agent_id=target, intent="consult", input=InputContent("work"))
        with pytest.raises(InvalidInput):
            await A2AService(tx).accept(tenant_id=principal.tenant_id, source_run_id=source.id,
                step_id="step", call_id="call", target_agent_id=target, intent="consult", input=InputContent("中" * 100000))
        assert not (await tx.session.scalars(select(A2ARequestRecord))).all()


async def test_explicit_answer_resumes_only_owned_target_with_ordered_main_locks(transaction_factory, monkeypatch):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    target_id = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=target, run_id=target_id,
            snapshot=snapshot(principal.tenant_id, target, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
    boundary = await step(transaction_factory, principal.tenant_id, target_id)
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=principal.tenant_id, run_id=target_id,
            payload=WaitingPayload("step", "wait", "Which source?", boundary), waiting_consumer=A2AService(tx))
    original = RunService.lock_main
    locks = []
    async def record_lock(self, *, tenant_id, run_id):
        locks.append(run_id)
        return await original(self, tenant_id=tenant_id, run_id=run_id)
    monkeypatch.setattr(RunService, "lock_main", record_lock)
    async with transaction_factory() as tx:
        changed = await A2AService(tx).answer(tenant_id=principal.tenant_id, request_id=request.id,
            source_run_id=source.id, step_id="step", call_id="call", waiting_reference="wait", input=InputContent("Public source"))
        assert changed.changed and changed.run.id == target_id and changed.run.status == "Running"
    assert locks == sorted((source.id, target_id))
    async with transaction_factory() as tx:
        repeated = await A2AService(tx).answer(tenant_id=principal.tenant_id, request_id=request.id,
            source_run_id=source.id, step_id="step", call_id="call", waiting_reference="wait", input=InputContent("Changed"))
        assert not repeated.changed
        with pytest.raises(AccessDenied):
            await A2AService(tx).answer(tenant_id=principal.tenant_id, request_id=request.id,
                source_run_id=target_id, step_id="step", call_id="call", waiting_reference="wait", input=InputContent("Wrong source"))


async def test_unsupported_persisted_version_is_not_silently_loaded(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    async with transaction_factory() as tx:
        row = await tx.session.get(A2ARequestRecord, request.id)
        row.payload_version = 2
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request.id)


async def test_opposite_agent_requests_answer_concurrently_without_lock_cycle(transaction_factory):
    principal, first_source, second_agent = await setup(transaction_factory)
    tenant = principal.tenant_id
    async with transaction_factory() as tx:
        await PermissionService(tx).set_visibility(principal, agent_id=first_source.agent_id, visibility="tenant")
    second_source_id = uuid4()
    second_source = (await start(transaction_factory, tenant, second_agent, run=second_source_id,
        snap=with_tools(snapshot(tenant, second_agent, second_source_id), "send_message_to_agent"))).run
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=tenant, run_id=second_source.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (
                ModelToolCall("call", "send_message_to_agent", "{}"),), "tool_calls", ModelUsage(), "step", False)))
    first = await accept(transaction_factory, principal, first_source, second_agent)
    second = await accept(transaction_factory, principal, second_source, first_source.agent_id)
    for request in (first, second):
        target_id = uuid4()
        async with transaction_factory() as tx:
            await RunService(tx).start(tenant_id=tenant, agent_id=request.target_agent_id, run_id=target_id,
                snapshot=snapshot(tenant, request.target_agent_id, target_id), input=request.input,
                source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
        boundary = await step(transaction_factory, tenant, target_id)
        async with transaction_factory() as tx:
            await RunService(tx).wait(tenant_id=tenant, run_id=target_id,
                payload=WaitingPayload("step", "wait", "Question", boundary), waiting_consumer=A2AService(tx))
    async def answer(request):
        async with transaction_factory() as tx:
            return await A2AService(tx).answer(tenant_id=tenant, request_id=request.id, source_run_id=request.source_run_id,
                step_id="step", call_id="call", waiting_reference="wait", input=InputContent("Answer"))
    async with asyncio.timeout(5):
        results = await asyncio.gather(answer(first), answer(second))
    assert all(result.changed and result.run.status == "Running" for result in results)


async def test_pending_result_and_source_input_commit_together_and_duplicate_delivery_is_inert(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    target_id = uuid4()
    async with transaction_factory() as tx:
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=target, run_id=target_id,
            snapshot=snapshot(principal.tenant_id, target, target_id), input=request.input,
            source=SourceIdentity("a2a", request.id, "target"), start_consumer=A2AService(tx))
        await RunService(tx).terminate(tenant_id=principal.tenant_id, run_id=target_id,
            status="Failed", reason="provider_unavailable", consumer=A2AService(tx))
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            changed = await A2AService(tx).deliver_pending(tenant_id=principal.tenant_id, request_id=request.id)
            assert changed.changed
            raise RuntimeError("rollback delivery")
    async with transaction_factory() as tx:
        assert (await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request.id)).source_delivery == "pending"
        history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=source.id)
        assert not any(isinstance(entry.payload, RelatedInputPayload) for entry in history.entries)
    async def deliver():
        async with transaction_factory() as tx:
            return await A2AService(tx).deliver_pending(tenant_id=principal.tenant_id, request_id=request.id)
    results = await asyncio.gather(deliver(), deliver())
    assert sum(result is not None and result.changed for result in results) == 1
    async with transaction_factory() as tx:
        history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=source.id)
        assert sum(isinstance(entry.payload, RelatedInputPayload) for entry in history.entries) == 1


async def test_failed_target_admission_produces_a_source_result_without_a_fake_run_reference(transaction_factory):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    async with transaction_factory() as tx:
        failed = await A2AService(tx).mark_admission_failed(tenant_id=principal.tenant_id, request_id=request.id, reason="capacity")
        assert failed.target_run_id is None and failed.source_delivery == "pending"
    async with transaction_factory() as tx:
        changed = await A2AService(tx).deliver_pending(tenant_id=principal.tenant_id, request_id=request.id)
        assert changed.changed
        history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=source.id)
        result = history.entries[-1].payload
        assert isinstance(result, RelatedInputPayload) and not result.input.references
        assert "admission_failed" in result.input.text
