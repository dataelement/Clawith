"""Session Tool calls use real Session/Run owners and bounded injected scheduling observation."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from modules.run.test_lifecycle import snapshot
from modules.session.test_session import accept, setup
from runtime.test_engine import Model, runtime, wait_status

from app.execution_dependencies.session_tools import SESSION_TOOL_DEFINITIONS, session_tool_bindings
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import ModelStepPayload, RunService, SourceIdentity, ToolBatchOutcome
from app.modules.session.public import SessionConsumers, SessionService
from app.modules.tool.public import (
    AuthorizedToolSet,
    CallScope,
    ResolvedTool,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScheduler,
)


def tool_snapshot(tenant, agent, run):
    return replace(snapshot(tenant, agent, run), tools=AuthorizedToolSet(tenant, agent,
        tuple(ResolvedTool(ToolDefinition(uuid4(), tenant, spec), None) for spec in SESSION_TOOL_DEFINITIONS)),
        initial_direct_names=frozenset(spec.name for spec in SESSION_TOOL_DEFINITIONS))


async def started(factory, principal, session, receipt):
    run_id = uuid4()
    async with factory() as tx:
        result = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=session.agent_id, run_id=run_id,
            snapshot=tool_snapshot(principal.tenant_id, session.agent_id, run_id), input=receipt.entry.content,
            source=SourceIdentity("session", session.id, str(receipt.link.id)), start_consumer=SessionConsumers())
    return result.run


class Committed:
    def __init__(self, factory):
        self.factory, self.changes = factory, []
    async def post_commit(self, changed):
        async with self.factory() as tx:
            actual = await RunService(tx).get(tenant_id=changed.run.tenant_id, run_id=changed.run.id)
            assert actual.status == changed.run.status and actual.latest_history_sequence == changed.run.latest_history_sequence
        self.changes.append(changed)


async def invoke(database, factory, run, observer, name, arguments, *, call_id="call", step_id="step", new_step=True):
    if new_step:
        async with factory() as tx:
            runs = RunService(tx)
            current = await runs.get(tenant_id=run.tenant_id, run_id=run.id)
            await runs.record_model_step(tenant_id=run.tenant_id, run_id=run.id,
                payload=ModelStepPayload(step_id, current.latest_history_sequence,
                    ModelStepResult("", (ModelToolCall(call_id, name, json.dumps(arguments)),), "tool_calls", ModelUsage(), step_id, False)))
    scope = CallScope(run.tenant_id, run.agent_id, run.id)
    binding = next(item for item in session_tool_bindings(sessions=database.sessions, scope=scope, step_id=step_id,
        runtime=observer, outcome_consumer=SessionConsumers(), role="main") if item.builtin.name == name)
    tool = ResolvedTool(ToolDefinition(uuid4(), run.tenant_id, binding.builtin), None)
    return await binding.executor.execute(tool, ToolCall(call_id, name, json.dumps(arguments)), scope)


async def test_history_uses_original_cutoff_and_large_entry_fragments(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    first = await accept(transaction_factory, p, session, text="汉" * 70000)
    run = await started(transaction_factory, p, session, first)
    await accept(transaction_factory, p, session, "later", "must not appear")
    observer = Committed(transaction_factory)
    fragments, offset = [], 0
    while True:
        result = await invoke(test_database, transaction_factory, run, observer, "session_history",
            {"after_position": 0, "content_offset": offset}, new_step=offset == 0)
        assert result.status == "success"
        data = json.loads(result.content_json)
        assert data["through_position"] == 1 and len(result.content_json.encode()) < 250000
        fragments.append(data["content_json"])
        if data["next_offset"] is None:
            break
        offset = data["next_offset"]
    assert json.loads("".join(fragments))["text"] == first.entry.content.text
    result = await invoke(test_database, transaction_factory, run, observer, "session_history",
        {"after_position": 1}, new_step=False)
    assert json.loads(result.content_json) == {"entry": None}
    assert observer.changes == []


async def test_work_supplement_preserves_human_origin_deduplicates_and_commits_before_wake(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    first = await accept(transaction_factory, p, session)
    target = await started(transaction_factory, p, session, first)
    second = await accept(transaction_factory, p, session, "control", "revise prior work")
    source = await started(transaction_factory, p, session, second)
    observer = Committed(transaction_factory)
    arguments = {"action": "supplement", "run_id": str(target.id), "text": "Use the revised requirements"}
    result = await invoke(test_database, transaction_factory, source, observer, "session_work", arguments)
    assert result.status == "success" and json.loads(result.content_json)["changed"]
    repeated = await invoke(test_database, transaction_factory, source, observer, "session_work", arguments, new_step=False)
    assert repeated.status == "success" and not json.loads(repeated.content_json)["changed"]
    async with transaction_factory() as tx:
        history = await RunService(tx).read_history(tenant_id=p.tenant_id, run_id=target.id)
    entry = history.entries[-1]
    assert entry.source.kind == "session_input" and entry.source.owner_id == second.entry.id
    assert "Agent-prepared" in entry.payload.input.text
    assert str(second.entry.id) in entry.payload.input.references[0].reference
    assert str(source.id) in entry.payload.input.references[1].reference
    assert len(history.entries) == 2 and len(observer.changes) == 2


async def test_same_session_only_and_real_origin_required(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    source = await started(transaction_factory, p, session, await accept(transaction_factory, p, session))
    async with transaction_factory() as tx:
        other_session = await SessionService(tx).create(p, agent_id=session.agent_id)
    other = await started(transaction_factory, p, other_session, await accept(transaction_factory, p, other_session))
    observer = Committed(transaction_factory)
    denied = await invoke(test_database, transaction_factory, source, observer, "session_work",
        {"action": "cancel", "run_id": str(other.id)})
    assert denied.status == "error" and observer.changes == []
    forged = await invoke(test_database, transaction_factory, source, observer, "session_work",
        {"action": "list"}, call_id="invented", new_step=False)
    assert forged.status == "error"
    assert session_tool_bindings(sessions=test_database.sessions, scope=CallScope(p.tenant_id, session.agent_id, source.id),
        step_id="step", runtime=observer, outcome_consumer=SessionConsumers(), role="sub") == ()
    async with transaction_factory() as tx:
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=other.id)).status == "Running"


async def test_opposite_supplements_lock_mains_in_one_order(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    first = await started(transaction_factory, p, session, await accept(transaction_factory, p, session))
    second = await started(transaction_factory, p, session, await accept(transaction_factory, p, session, "second"))
    observer = Committed(transaction_factory)
    results = await asyncio.wait_for(asyncio.gather(
        invoke(test_database, transaction_factory, first, observer, "session_work",
            {"action": "supplement", "run_id": str(second.id), "text": "first supplement"}),
        invoke(test_database, transaction_factory, second, observer, "session_work",
            {"action": "supplement", "run_id": str(first.id), "text": "second supplement"})), 5)
    assert all(result.status == "success" for result in results)


async def test_self_cancel_commits_product_result_and_does_not_leave_late_tool_pending(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run_id = uuid4()
    class Batches:
        async def execute(self, *, snapshot, step_id, available, calls):
            scope = CallScope(snapshot.tenant_id, snapshot.agent_id, snapshot.workspace.run_id)
            bindings = session_tool_bindings(sessions=test_database.sessions, scope=scope, step_id=step_id,
                runtime=engine, outcome_consumer=SessionConsumers(), role=snapshot.role)
            scheduler = ToolScheduler(ToolRegistry(bindings), max_parallel=1, timeout_seconds=5)
            results = await scheduler.execute(available,
                tuple(ToolCall(call.call_id, call.name, call.arguments_json) for call in calls), scope)
            return ToolBatchOutcome(results, available)
    async def reply(request):
        return ModelStepResult("", (ModelToolCall("cancel", "session_work",
            json.dumps({"action": "cancel", "run_id": str(run_id)})),), "tool_calls", ModelUsage(), request.step_id, False)
    model = Model(reply)
    engine = runtime(test_database, model, Batches(), consumer=SessionConsumers(), start_consumer=SessionConsumers())
    await engine.startup()
    try:
        await engine.start(snapshot=tool_snapshot(p.tenant_id, session.agent_id, run_id), input=receipt.entry.content,
            source=SourceIdentity("session", session.id, str(receipt.link.id)))
        await wait_status(test_database, p.tenant_id, run_id, "Cancelled")
        async with asyncio.timeout(5):
            while engine.dispatcher.active:
                await asyncio.sleep(.01)
        assert run_id not in engine._pending and engine.dispatcher.admitted == 0 and engine.dispatcher.failures == {}
        async with transaction_factory() as tx:
            link = await SessionService(tx).get_link(p, session_id=session.id, link_id=receipt.link.id)
            assert link.result.status == "Cancelled"
            history = await RunService(tx).read_history(tenant_id=p.tenant_id, run_id=run_id)
            assert not any(type(entry.payload).__name__ == "ToolResultPayload" for entry in history.entries)
    finally:
        await engine.close()


async def test_list_inspect_and_cancel_keep_distinct_main_results(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    target_receipt = await accept(transaction_factory, p, session)
    target = await started(transaction_factory, p, session, target_receipt)
    source = await started(transaction_factory, p, session, await accept(transaction_factory, p, session, "control"))
    observer = Committed(transaction_factory)
    listing = await invoke(test_database, transaction_factory, source, observer, "session_work", {"action": "list"})
    assert {item["run_id"] for item in json.loads(listing.content_json)["work"]} == {str(source.id), str(target.id)}
    inspected = await invoke(test_database, transaction_factory, source, observer, "session_work",
        {"action": "inspect", "run_id": str(target.id)}, step_id="inspect")
    assert json.loads(inspected.content_json)["history"]["kind"] == "initial_input"
    cancelled = await invoke(test_database, transaction_factory, source, observer, "session_work",
        {"action": "cancel", "run_id": str(target.id)}, step_id="cancel")
    assert json.loads(cancelled.content_json)["run"]["status"] == "Cancelled"
    async with transaction_factory() as tx:
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=source.id)).status == "Running"
        assert (await SessionService(tx).get_link(p, session_id=session.id, link_id=target_receipt.link.id)).result.status == "Cancelled"


async def test_cancel_consumer_failure_rolls_back_and_never_publishes_schedule(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    target_receipt = await accept(transaction_factory, p, session)
    target = await started(transaction_factory, p, session, target_receipt)
    source = await started(transaction_factory, p, session, await accept(transaction_factory, p, session, "control"))
    observer = Committed(transaction_factory)
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=source.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "session_work", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
    class Reject:
        async def record_outcome(self, tx, *, run, outcome):
            await SessionConsumers().record_outcome(tx, run=run, outcome=outcome)
            raise RuntimeError("consumer failed")
    scope = CallScope(p.tenant_id, session.agent_id, source.id)
    binding = next(value for value in session_tool_bindings(sessions=test_database.sessions, scope=scope, step_id="step",
        runtime=observer, outcome_consumer=Reject(), role="main") if value.builtin.name == "session_work")
    tool = ResolvedTool(ToolDefinition(uuid4(), p.tenant_id, binding.builtin), None)
    with pytest.raises(RuntimeError, match="consumer failed"):
        await binding.executor.execute(tool, ToolCall("call", "session_work", json.dumps({"action": "cancel", "run_id": str(target.id)})), scope)
    async with transaction_factory() as tx:
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=target.id)).status == "Running"
        assert (await SessionService(tx).get_link(p, session_id=session.id, link_id=target_receipt.link.id)).result is None
    assert observer.changes == []


async def test_external_cancel_during_tool_discards_late_result_without_reexecution(test_database, transaction_factory):
    from runtime.test_engine import Tools, with_tools

    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = uuid4()
    entered = asyncio.Event()
    async def reply(request):
        return ModelStepResult("", (ModelToolCall("work", "read_file", "{}"),), "tool_calls", ModelUsage(), request.step_id, False)
    async def tool_result(snapshot, step_id, available, calls):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ToolBatchOutcome((ToolResult("work", "uncertain", '{"message":"effect may have occurred"}'),), available)
    tools = Tools(tool_result)
    engine = runtime(test_database, Model(reply), tools, consumer=SessionConsumers(), start_consumer=SessionConsumers())
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(p.tenant_id, session.agent_id, run), "read_file"), input=receipt.entry.content,
            source=SourceIdentity("session", session.id, str(receipt.link.id)))
        await asyncio.wait_for(entered.wait(), 2)
        await engine.cancel(tenant_id=p.tenant_id, run_id=run)
        assert len(tools.calls) == 1 and run not in engine._pending and engine.dispatcher.active == 0
        async with transaction_factory() as tx:
            history = await RunService(tx).read_history(tenant_id=p.tenant_id, run_id=run)
            assert history.entries[-1].payload.status == "Cancelled"
            assert not any(type(entry.payload).__name__ == "ToolResultPayload" for entry in history.entries)
    finally:
        await engine.close()


@pytest.mark.parametrize("name,arguments", [
    ("session_history", {"through_position": 999}),
    ("session_history", {"after_position": True}),
    ("session_work", {"action": "cancel"}),
    ("session_work", {"action": "list", "session_id": "forged"}),
    ("session_work", {"action": "list", "limit": 101}),
    ("session_work", {"action": "cancel", "run_id": "not a UUID"}),
])
async def test_invalid_fields_and_model_selected_destinations_are_rejected(test_database, transaction_factory, name, arguments):
    p, session = await setup(transaction_factory)
    source = await started(transaction_factory, p, session, await accept(transaction_factory, p, session))
    observer = Committed(transaction_factory)
    result = await invoke(test_database, transaction_factory, source, observer, name, arguments)
    assert result.status == "error" and observer.changes == []
