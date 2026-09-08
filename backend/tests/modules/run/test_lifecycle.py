"""Real PostgreSQL lifecycle transactions; scheduler and providers are not fixtures here."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.modules.model.public import (
    ModelContextProfile,
    ModelStepResult,
    ModelToolCall,
    ModelUsage,
    PrivateModelPolicy,
    ResolvedModel,
)
from app.modules.run.public import (
    InputContent,
    ModelStepPayload,
    RunService,
    SourceIdentity,
    ToolResultPayload,
    WaitingPayload,
)
from app.modules.run.snapshot import AgentIdentity, PlatformInstructions, RunSnapshot, derive_child
from app.modules.tool.public import AuthorizedToolSet, ToolResult
from app.modules.workspace.public import SkillDiscovery, WorkspaceScope, WorkspaceSubject


def snapshot(tenant, agent, run):
    model_id = uuid4()
    model = ResolvedModel(PrivateModelPolicy(tenant, model_id, "openai", "openai_responses", "test",
        "https://provider.invalid/v1", uuid4(), 10000, 1000, '{"supports_tool_calling":true}', '{"protocol":"openai_responses"}'),
        ModelContextProfile(model_id, "openai", "test", 10000, 1000, False, False, False))
    return RunSnapshot(tenant_id=tenant, agent_id=agent, role="main",
        platform=PlatformInstructions("v1", "platform"), agent=AgentIdentity("agent", "soul", "UTC"),
        model=model, tools=AuthorizedToolSet(tenant, agent, ()), initial_direct_names=frozenset(),
        workspace=WorkspaceScope(tenant, agent, WorkspaceSubject("agent", agent), run),
        skills=SkillDiscovery(tenant, agent, ()))


async def seed(transaction_factory):
    async with transaction_factory() as tx:
        records = await _seed_to_agent(tx.session)
        return records["tenant"].id, records["agent"].id


async def start(transaction_factory, tenant, agent, *, run=None, source=None, snap=None, parent=None):
    run = run or uuid4()
    async with transaction_factory() as tx:
        return await RunService(tx).start(tenant_id=tenant, agent_id=agent, run_id=run,
            source=source or SourceIdentity("session", uuid4(), "query"), input=InputContent("work"),
            snapshot=snap or snapshot(tenant, agent, run), parent_run_id=parent)


async def step(transaction_factory, tenant, run, step_id="step", boundary=None):
    async with transaction_factory() as tx:
        service = RunService(tx)
        boundary = boundary or (await service.get(tenant_id=tenant, run_id=run)).latest_history_sequence
        await service.record_model_step(tenant_id=tenant, run_id=run,
            payload=ModelStepPayload(step_id, boundary, ModelStepResult("done", (), "stop", ModelUsage(), step_id, False)))
        return boundary


async def family(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    child_id = uuid4()
    async with transaction_factory() as tx:
        parent_snapshot = await RunService(tx).read_snapshot(tenant_id=tenant, run_id=main.id)
    child = (await start(transaction_factory, tenant, agent, run=child_id,
        snap=derive_child(parent_snapshot, run_id=child_id), parent=main.id,
        source=SourceIdentity("task", main.id, "step:call"))).run
    return tenant, agent, main, child


async def test_start_atomic_snapshot_initial_input_and_tenant_scope(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    result = await start(transaction_factory, tenant, agent)
    assert result.created and result.run.status == "Running" and result.run.latest_history_sequence == 1
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.read_snapshot(tenant_id=tenant, run_id=result.run.id)).agent_id == agent
        assert (await service.read_history(tenant_id=tenant, run_id=result.run.id)).entries[0].payload.input.text == "work"
        with pytest.raises(NotFound):
            await service.get(tenant_id=uuid4(), run_id=result.run.id)
    run = uuid4()
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).start(tenant_id=tenant, agent_id=agent, run_id=run,
                source=SourceIdentity("session", uuid4(), "rollback"), input=InputContent("work"),
                snapshot=snapshot(tenant, agent, run))
            raise RuntimeError("rollback")
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await RunService(tx).get(tenant_id=tenant, run_id=run)


async def test_concurrent_duplicate_start_creates_one_run(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    identity = SourceIdentity("session", uuid4(), "same")
    results = await asyncio.gather(*(start(transaction_factory, tenant, agent, source=identity) for _ in range(6)))
    assert sum(item.created for item in results) == 1
    assert len({item.run.id for item in results}) == 1


async def test_snapshot_identity_and_child_inheritance_are_enforced(transaction_factory):
    tenant, agent, main, child = await family(transaction_factory)
    run = uuid4()
    with pytest.raises(InvalidInput):
        await start(transaction_factory, tenant, agent, run=run, snap=snapshot(tenant, agent, uuid4()))
    async with transaction_factory() as tx:
        service = RunService(tx)
        parent_snap = await service.read_snapshot(tenant_id=tenant, run_id=main.id)
        modified = replace(derive_child(parent_snap, run_id=run), platform=PlatformInstructions("v2", "changed"))
        with pytest.raises(InvalidInput):
            await service.start(tenant_id=tenant, agent_id=agent, run_id=run, snapshot=modified,
                source=SourceIdentity("task", main.id, "second"), input=InputContent("work"), parent_run_id=main.id)
        with pytest.raises(InvalidInput):
            await service.start(tenant_id=tenant, agent_id=agent, run_id=run, snapshot=modified,
                source=SourceIdentity("task", child.id, "recursive"), input=InputContent("work"), parent_run_id=child.id)


async def test_unseen_related_input_prevents_wait_and_complete_but_own_history_does_not(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    boundary = await step(transaction_factory, tenant, main.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.append_related(tenant_id=tenant, run_id=main.id, input=InputContent("new"),
            source=SourceIdentity("session", uuid4(), "next"))
        waiting = await service.wait(tenant_id=tenant, run_id=main.id, payload=WaitingPayload("step", "wait", "why", boundary))
        complete = await service.complete(tenant_id=tenant, run_id=main.id, step_id="step", output="old")
        assert not waiting.changed and not complete.changed and complete.run.status == "Running"
    await step(transaction_factory, tenant, main.id, "step2")
    async with transaction_factory() as tx:
        completed = await RunService(tx).complete(tenant_id=tenant, run_id=main.id, step_id="step2", output="new")
        assert completed.changed and completed.run.status == "Completed"


async def test_stale_model_decision_and_wrong_read_boundary_rejected(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    await step(transaction_factory, tenant, main.id)
    boundary = await step(transaction_factory, tenant, main.id, "step2")
    async with transaction_factory() as tx:
        service = RunService(tx)
        with pytest.raises(Conflict):
            await service.complete(tenant_id=tenant, run_id=main.id, step_id="step", output="stale")
        with pytest.raises(InvalidInput):
            await service.wait(tenant_id=tenant, run_id=main.id, payload=WaitingPayload("step2", "wait", "why", boundary - 1))


async def test_child_wait_notifies_parent_and_resumes_same_child(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    main_boundary = await step(transaction_factory, tenant, main.id)
    child_boundary = await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.wait(tenant_id=tenant, run_id=main.id, payload=WaitingPayload("step", "parent-wait", "", main_boundary))
        waiting = await service.wait(tenant_id=tenant, run_id=child.id,
            payload=WaitingPayload("step", "child-wait", "Need date", child_boundary))
        assert waiting.run.status == "Waiting" and waiting.wake_run_ids == (main.id,)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        resumed = await service.append_related(tenant_id=tenant, run_id=child.id, input=InputContent("Monday"),
            source=SourceIdentity("parent_answer", main.id, "answer"), waiting_reference="child-wait")
        assert resumed.run.id == child.id and resumed.run.status == "Running"
        repeated = await service.append_related(tenant_id=tenant, run_id=child.id, input=InputContent("Monday"),
            source=SourceIdentity("parent_answer", main.id, "answer"), waiting_reference="child-wait")
        assert not repeated.changed and not repeated.wake_run_ids


async def test_child_completion_wakes_parent_and_parent_termination_cancels_child(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        result = await service.complete(tenant_id=tenant, run_id=child.id, step_id="step", output="finished")
        assert result.terminal_run_ids == (child.id,) and result.wake_run_ids == (main.id,)
        assert (await service.read_history(tenant_id=tenant, run_id=main.id)).entries[-1].source.owner_id == child.id
    tenant, _, main, child = await family(transaction_factory)
    async with transaction_factory() as tx:
        service = RunService(tx)
        result = await service.terminate(tenant_id=tenant, run_id=main.id, status="Failed", reason="failed")
        assert set(result.terminal_run_ids) == {main.id, child.id}
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Cancelled"
        assert result.wake_run_ids == ()


async def test_consumer_failure_rolls_back_family_and_retry_does_not_repeat_consumer(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    calls = []
    class Consumer:
        async def record_outcome(self, transaction, *, run, outcome):
            calls.append(run.id)
            if len(calls) == 1:
                raise RuntimeError("owner failed")
    consumer = Consumer()
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).terminate(tenant_id=tenant, run_id=main.id, status="Failed", reason="failure", consumer=consumer)
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Running"
        await service.terminate(tenant_id=tenant, run_id=main.id, status="Failed", reason="failure", consumer=consumer)
    count = len(calls)
    assert calls == [main.id, main.id]
    async with transaction_factory() as tx:
        result = await RunService(tx).terminate(tenant_id=tenant, run_id=main.id, status="Failed", reason="failure", consumer=consumer)
        assert not result.changed and len(calls) == count


async def test_shutdown_interrupts_waiting_and_running_family_without_parent_wakeup(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    boundary = await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.wait(tenant_id=tenant, run_id=child.id, payload=WaitingPayload("step", "wait", "question", boundary))
        before = (await service.get(tenant_id=tenant, run_id=main.id)).latest_history_sequence
        affected = await service.interrupt_batch(limit=1)
        assert {row.id for row in affected} == {main.id, child.id}
        assert all(row.status == "Interrupted" for row in affected)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).latest_history_sequence == before + 1
        assert await service.interrupt_batch() == ()
        with pytest.raises(Conflict):
            await service.append_related(tenant_id=tenant, run_id=child.id,
                input=InputContent("revive"), source=SourceIdentity("parent_answer", main.id, "revive"))


async def test_duplicate_input_and_model_step_do_not_append_or_schedule_again(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    identity = SourceIdentity("session", uuid4(), "answer")
    async with transaction_factory() as tx:
        service = RunService(tx)
        first = await service.append_related(tenant_id=tenant, run_id=main.id, input=InputContent("first"), source=identity)
        again = await service.append_related(tenant_id=tenant, run_id=main.id, input=InputContent("changed"), source=identity)
        assert first.changed and not again.changed and not again.wake_run_ids
        assert first.run.latest_history_sequence == again.run.latest_history_sequence
        payload = ModelStepPayload("same", 2, ModelStepResult("done", (), "stop", ModelUsage(), "interaction", False))
        recorded = await service.record_model_step(tenant_id=tenant, run_id=main.id, payload=payload)
        repeated = await service.record_model_step(tenant_id=tenant, run_id=main.id, payload=payload)
        assert recorded.appended and not repeated.appended


async def test_tool_results_require_matching_call_and_deduplicate(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.record_model_step(tenant_id=tenant, run_id=main.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "read", "{}"),),
                "tool_calls", ModelUsage(), "interaction", False)))
        with pytest.raises(InvalidInput):
            await service.record_tool_result(tenant_id=tenant, run_id=main.id,
                payload=ToolResultPayload("step", "other", ToolResult("call", "success", "{}")))
        payload = ToolResultPayload("step", "read", ToolResult("call", "success", "{}"))
        first = await service.record_tool_result(tenant_id=tenant, run_id=main.id, payload=payload)
        again = await service.record_tool_result(tenant_id=tenant, run_id=main.id, payload=payload)
        assert first.appended and not again.appended


async def test_input_committed_while_completion_waits_on_row_lock_wins(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    await step(transaction_factory, tenant, main.id)
    async def complete():
        async with transaction_factory() as tx:
            return await RunService(tx).complete(tenant_id=tenant, run_id=main.id, step_id="step", output="stale")
    async with transaction_factory() as tx:
        await RunService(tx).append_related(tenant_id=tenant, run_id=main.id, input=InputContent("concurrent input"),
            source=SourceIdentity("session", uuid4(), "new"))
        pending = asyncio.create_task(complete())
        await asyncio.sleep(0.05)
        assert not pending.done()
    outcome = await asyncio.wait_for(pending, 2)
    assert not outcome.changed and outcome.run.status == "Running"


async def test_parent_cancel_races_child_wait_without_deadlock_or_resurrection(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    boundary = await step(transaction_factory, tenant, child.id)
    async def wait_child():
        try:
            async with transaction_factory() as tx:
                return await RunService(tx).wait(tenant_id=tenant, run_id=child.id,
                    payload=WaitingPayload("step", "child-wait", "question", boundary))
        except Conflict:
            return None
    async def cancel_main():
        async with transaction_factory() as tx:
            return await RunService(tx).terminate(tenant_id=tenant, run_id=main.id, status="Cancelled", reason="stop")
    await asyncio.wait_for(asyncio.gather(wait_child(), cancel_main()), 3)
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Cancelled"
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Cancelled"


async def test_large_child_output_preserves_full_history_and_bounds_parent_preview(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    await step(transaction_factory, tenant, child.id)
    output = "结果" * 100000
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.complete(tenant_id=tenant, run_id=child.id, step_id="step", output=output)
        child_history = await service.read_history(tenant_id=tenant, run_id=child.id)
        parent_history = await service.read_history(tenant_id=tenant, run_id=main.id)
        assert child_history.entries[-1].payload.output == output
        summary = parent_history.entries[-1].payload.input
        assert len(summary.text.encode()) < 8500
        assert "truncated" in summary.text
        assert summary.references[0].reference.startswith(f"run:{child.id}:")


async def test_interruption_rollback_preserves_both_family_members(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            assert len(await RunService(tx).interrupt_batch(limit=1)) == 2
            raise RuntimeError("commit failed")
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Running"
        for limit in (0, 101, True):
            with pytest.raises(InvalidInput):
                await service.interrupt_batch(limit=limit)


async def test_waiting_requires_resume_and_direct_human_child_input_is_rejected(transaction_factory):
    tenant, _, _, child = await family(transaction_factory)
    boundary = await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.wait(tenant_id=tenant, run_id=child.id, payload=WaitingPayload("step", "wait", "why", boundary))
        repeated = await service.wait(tenant_id=tenant, run_id=child.id, payload=WaitingPayload("step", "wait", "why", boundary))
        assert not repeated.changed and repeated.run.status == "Waiting"
        with pytest.raises(Conflict):
            await service.wait(tenant_id=tenant, run_id=child.id, payload=WaitingPayload("step", "other", "why", boundary))
        with pytest.raises(Conflict):
            await service.complete(tenant_id=tenant, run_id=child.id, step_id="step", output="premature")
        with pytest.raises(Conflict):
            await service.record_tool_result(tenant_id=tenant, run_id=child.id,
                payload=ToolResultPayload("step", "read", ToolResult("call", "success", "{}")))
        with pytest.raises(InvalidInput):
            await service.append_related(tenant_id=tenant, run_id=child.id,
                input=InputContent("direct user message"), source=SourceIdentity("session", uuid4(), "input"))


async def test_shutdown_preserves_terminal_root_but_cleans_remaining_child(transaction_factory):
    # Stored inconsistent family is still safely stopped; shutdown never rewrites its terminal Parent.
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.modules.run.models import RunRecord

    tenant, _, main, child = await family(transaction_factory)
    async with transaction_factory() as tx:
        await tx.session.execute(update(RunRecord).where(RunRecord.id == main.id).values(
            status="Failed", finished_at=datetime.now(UTC)))
    async with transaction_factory() as tx:
        service = RunService(tx)
        affected = await service.interrupt_batch()
        assert len(affected) == 1 and affected[0].id == child.id and affected[0].status == "Interrupted"
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Failed"


async def test_source_lookup_is_tenant_scoped_and_preserves_terminal_idempotency(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    identity = SourceIdentity("session", uuid4(), "query")
    main = (await start(transaction_factory, tenant, agent, source=identity)).run
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.find_by_source(tenant_id=tenant, source=identity)).id == main.id
        assert await service.find_by_source(tenant_id=uuid4(), source=identity) is None
        await service.terminate(tenant_id=tenant, run_id=main.id, status="Cancelled", reason="stop")
    duplicate = await start(transaction_factory, tenant, agent, source=identity)
    assert not duplicate.created and duplicate.run.id == main.id and duplicate.run.status == "Cancelled"


async def test_atomic_family_size_bound_fails_without_partial_settlement(transaction_factory, monkeypatch):
    import app.modules.run.lifecycle as run_public

    tenant, _, main, child = await family(transaction_factory)
    monkeypatch.setattr(run_public, "MAX_TRANSACTION_RUNS", 1)
    with pytest.raises(Conflict, match="bound"):
        async with transaction_factory() as tx:
            await RunService(tx).interrupt_batch()
    with pytest.raises(Conflict, match="bound"):
        async with transaction_factory() as tx:
            await RunService(tx).terminate(tenant_id=tenant, run_id=main.id, status="Cancelled", reason="stop")
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Running"


async def test_completed_decision_serializes_late_input_rejection(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    await step(transaction_factory, tenant, main.id)
    async def late_input():
        with pytest.raises(Conflict):
            async with transaction_factory() as tx:
                await RunService(tx).append_related(tenant_id=tenant, run_id=main.id, input=InputContent("late"),
                    source=SourceIdentity("session", uuid4(), "late"))
    async with transaction_factory() as tx:
        await RunService(tx).complete(tenant_id=tenant, run_id=main.id, step_id="step", output="done")
        pending = asyncio.create_task(late_input())
        await asyncio.sleep(0.05)
        assert not pending.done()
    await asyncio.wait_for(pending, 2)


async def test_wait_for_tasks_requires_a_current_child_and_subagents_cannot_use_it(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = (await start(transaction_factory, tenant, agent)).run
    boundary = await step(transaction_factory, tenant, main.id)
    async with transaction_factory() as tx:
        result = await RunService(tx).wait(tenant_id=tenant, run_id=main.id,
            payload=WaitingPayload("step", "tasks", "", boundary))
        assert not result.changed and result.run.status == "Running"
    tenant, _, _, child = await family(transaction_factory)
    boundary = await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput):
            await RunService(tx).wait(tenant_id=tenant, run_id=child.id,
                payload=WaitingPayload("step", "tasks", "", boundary))


async def test_large_history_entry_can_be_inspected_losslessly_in_bounded_fragments(transaction_factory):
    import json

    tenant, _, _, child = await family(transaction_factory)
    await step(transaction_factory, tenant, child.id)
    output = "汉字🌍" * 40000
    async with transaction_factory() as tx:
        service = RunService(tx)
        completed = await service.complete(tenant_id=tenant, run_id=child.id, step_id="step", output=output)
        before = completed.run.latest_history_sequence - 1
        pieces, offset = [], 0
        while True:
            fragment = await service.read_history_fragment(tenant_id=tenant, run_id=child.id,
                after_sequence=before, content_offset=offset)
            assert fragment is not None and fragment.kind == "terminal_outcome"
            assert len(fragment.content_json_fragment.encode()) <= 64000
            pieces.append(fragment.content_json_fragment)
            if fragment.next_offset is None:
                assert fragment.next_after_sequence == before + 1
                break
            assert fragment.next_after_sequence == before
            offset = fragment.next_offset
        assert len(pieces) > 1 and json.loads("".join(pieces))["output"] == output
        assert await service.read_history_fragment(tenant_id=tenant, run_id=child.id,
            after_sequence=before + 1) is None


async def test_fragment_scope_bounds_unknown_versions_and_gaps_fail_explicitly(transaction_factory):
    from sqlalchemy import update

    from app.modules.run.contracts import InvalidHistory
    from app.modules.run.models import RunHistoryRecord

    tenant, agent = await seed(transaction_factory)
    run = (await start(transaction_factory, tenant, agent)).run
    async with transaction_factory() as tx:
        service = RunService(tx)
        with pytest.raises(NotFound):
            await service.read_history_fragment(tenant_id=uuid4(), run_id=run.id)
        for values in ({"after_sequence": -1}, {"content_offset": -1}, {"content_offset": 9999},
                {"max_characters": 0}, {"max_characters": 16001}, {"max_characters": True}, {"after_sequence": 2}):
            with pytest.raises(InvalidInput):
                await service.read_history_fragment(tenant_id=tenant, run_id=run.id, **values)
        first = await service.read_history_fragment(tenant_id=tenant, run_id=run.id, max_characters=1)
        assert first is not None and len(first.content_json_fragment) == 1 and first.next_offset == 1
        await tx.session.execute(update(RunHistoryRecord).where(RunHistoryRecord.run_id == run.id).values(payload_schema_version=2))
        with pytest.raises(InvalidHistory, match="unsupported"):
            await service.read_history_fragment(tenant_id=tenant, run_id=run.id)


async def test_fresh_main_initialization_uses_four_statements_and_admits_only_new_source(test_database, transaction_factory):
    from sqlalchemy import event

    tenant, agent = await seed(transaction_factory)
    run, source = uuid4(), SourceIdentity("session", uuid4(), "four-sql")
    statements, admissions = [], []
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", observe)
    try:
        async with transaction_factory() as tx:
            result = await RunService(tx).start(tenant_id=tenant, agent_id=agent, run_id=run,
                source=source, input=InputContent("first"), snapshot=snapshot(tenant, agent, run),
                admit=lambda: admissions.append(True))
        assert result.created and result.run.latest_history_sequence == 1
        assert len(statements) == 4 and len(admissions) == 1
        assert sum(statement.lstrip().upper().startswith("SELECT") for statement in statements) == 1
        assert sum(statement.lstrip().upper().startswith("INSERT") for statement in statements) == 3
        assert not any("FOR UPDATE" in statement or statement.lstrip().upper().startswith("UPDATE") for statement in statements)
        statements.clear()
        async with transaction_factory() as tx:
            duplicate = await RunService(tx).start(tenant_id=tenant, agent_id=agent, run_id=run,
                source=source, input=InputContent("retry changed"), snapshot=snapshot(tenant, agent, run),
                admit=lambda: admissions.append(True))
        assert not duplicate.created and duplicate.run == result.run
        assert len(statements) == 1 and len(admissions) == 1
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", observe)


async def test_fresh_main_snapshot_storage_failure_rolls_back_all_three_records(test_database, transaction_factory):
    from sqlalchemy import event, func, select

    from app.modules.run.models import RunHistoryRecord, RunRecord, RunSnapshotRecord

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    def fail_snapshot(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and "agent_run_snapshots" in statement:
            raise RuntimeError("snapshot storage failed")
    event.listen(test_database.engine.sync_engine, "after_cursor_execute", fail_snapshot)
    try:
        with pytest.raises(RuntimeError, match="snapshot storage failed"):
            await start(transaction_factory, tenant, agent, run=run)
    finally:
        event.remove(test_database.engine.sync_engine, "after_cursor_execute", fail_snapshot)
    async with transaction_factory() as tx:
        assert await tx.session.scalar(select(func.count()).select_from(RunRecord).where(RunRecord.id == run)) == 0
        assert await tx.session.scalar(select(func.count()).select_from(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run)) == 0
        assert await tx.session.scalar(select(func.count()).select_from(RunHistoryRecord).where(RunHistoryRecord.run_id == run)) == 0


async def test_service_interruption_consumes_main_outcome_atomically_and_never_delivers_child_result(test_database, transaction_factory):
    from sqlalchemy import Column, MetaData, String, Table, Uuid, func, insert, select

    tenant, _, main, child = await family(transaction_factory)
    table = Table("fixture_interrupt_outcomes", MetaData(), Column("run_id", Uuid, primary_key=True),
        Column("status", String), schema=test_database.schema)
    async with test_database.engine.begin() as connection:
        await connection.run_sync(table.create)
    seen = []
    class Consumer:
        reject = True
        async def record_outcome(self, tx, *, run, outcome):
            assert run.id == main.id and outcome.status == "Interrupted"
            await tx.session.execute(insert(table).values(run_id=run.id, status=outcome.status))
            if self.reject:
                raise RuntimeError("owner unavailable")
            seen.append(run.id)
    consumer = Consumer()
    with pytest.raises(RuntimeError, match="owner unavailable"):
        async with transaction_factory() as tx:
            await RunService(tx).interrupt_batch(consumer=consumer)
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        assert (await service.get(tenant_id=tenant, run_id=child.id)).status == "Running"
        assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
        consumer.reject = False
        ended = await service.interrupt_batch(consumer=consumer)
        assert {row.id for row in ended} == {main.id, child.id}
        history = await service.read_history(tenant_id=tenant, run_id=main.id)
        assert [type(entry.payload).__name__ for entry in history.entries] == ["InitialInputPayload", "TerminalOutcomePayload"]
    async with transaction_factory() as tx:
        assert await RunService(tx).interrupt_batch(consumer=consumer) == ()
        assert await tx.session.scalar(select(func.count()).select_from(table)) == 1
    assert seen == [main.id]
