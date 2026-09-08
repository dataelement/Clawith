"""Real Run/History/Snapshot transactions with controlled Model and Tool ports."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from modules.run.test_lifecycle import family, seed, snapshot
from sqlalchemy.exc import SQLAlchemyError

from app.infrastructure.errors import Conflict, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.model.public import ModelLimits, ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.engine import RunRuntime, ToolBatchOutcome
from app.modules.run.public import InputContent, RunService, SourceIdentity
from app.modules.tool.public import AuthorizedToolSet, DefinitionSpec, ResolvedTool, ToolDefinition, ToolResult


class Model:
    operation_limits = ModelLimits()

    def __init__(self, callback=None):
        self.requests = []
        self.released = []
        self.callback = callback

    async def execute_step(self, policy, request, *, on_event=None):
        self.requests.append(request)
        if self.callback:
            return await self.callback(request)
        return ModelStepResult("finished", (), "stop", ModelUsage(), request.step_id, False)

    async def release_continuation(self, **values):
        self.released.append(values)


class Tools:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    async def execute(self, *, snapshot, step_id, available, calls):
        self.calls.extend(calls)
        if self.callback:
            return await self.callback(snapshot, step_id, available, calls)
        return ToolBatchOutcome(tuple(ToolResult(call.call_id, "success", '{"value":"read"}') for call in calls), available)


def with_tools(snap, *names):
    captured = tuple(ResolvedTool(ToolDefinition(uuid4(), snap.tenant_id,
        DefinitionSpec(name, f"Use {name}", '{"type":"object"}', f"{name}.v1", "builtin")), None) for name in names)
    return replace(snap, tools=AuthorizedToolSet(snap.tenant_id, snap.agent_id, captured),
        initial_direct_names=frozenset(names))


def runtime(database, model=None, tools=None, **kwargs):
    return RunRuntime(control_sessions=database.sessions, execution_sessions=database.sessions,
        model=model or Model(), tools=tools or Tools(), **kwargs)


async def status(database, tenant, run):
    async with transaction(database.sessions) as tx:
        return await RunService(tx).get(tenant_id=tenant, run_id=run)


async def wait_status(database, tenant, run, expected):
    async with asyncio.timeout(5):
        while True:
            value = await status(database, tenant, run)
            if value.status == expected:
                return value
            await asyncio.sleep(0.01)


async def test_real_start_executes_one_model_and_commits_trace_before_completion(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    model = Model()
    engine = runtime(test_database, model)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("question"),
            source=SourceIdentity("session", uuid4(), "query"))
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 1
        async with transaction(test_database.sessions) as tx:
            page = await RunService(tx).read_history(tenant_id=tenant, run_id=run)
        assert [type(entry.payload).__name__ for entry in page.entries] == [
            "InitialInputPayload", "ModelInputPayload", "ModelStepPayload", "TerminalOutcomePayload"]
        assert page.entries[2].payload.read_through_sequence == 1
        assert any("question" in part.value for message in model.requests[0].messages for part in message.content)
    finally:
        await engine.close()
    assert engine.dispatcher.admitted == 0 and len(model.released) == 1


async def test_tool_batch_is_separate_quantum_and_next_request_has_complete_exchange(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = (ModelToolCall("read-call", "read_file", "{}"),) if len(model.requests) == 1 else ()
        return ModelStepResult("", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    model = Model(reply)
    tools = Tools()
    engine = runtime(test_database, model, tools)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "read_file"), input=InputContent("read"),
            source=SourceIdentity("session", uuid4(), "query"))
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 2 and len(tools.calls) == 1
        assert [message.role for message in model.requests[1].messages] == ["system", "user", "assistant", "tool"]
    finally:
        await engine.close()


async def test_waiting_releases_cache_not_admission_and_resume_keeps_context(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = (ModelToolCall("ask", "need_input", "{}"),) if len(model.requests) == 1 else ()
        return ModelStepResult("answer", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    async def tool_reply(snap, step, available, calls):
        return ToolBatchOutcome((ToolResult("ask", "success", '{"need_input":true,"question":"which day?"}'),), available)
    model = Model(reply)
    engine = runtime(test_database, model, Tools(tool_reply), capacity=1, slots=1)
    await engine.startup()
    identity = SourceIdentity("session", uuid4(), "query")
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "need_input"), input=InputContent("schedule"), source=identity)
        waiting = await wait_status(test_database, tenant, run, "Waiting")
        async with asyncio.timeout(2):
            while run in engine._caches:
                await asyncio.sleep(0.01)
        assert engine.dispatcher.admitted == 1
        duplicate = await engine.start(snapshot=with_tools(snapshot(tenant, agent, uuid4()), "need_input"), input=InputContent("retry"), source=identity)
        assert not duplicate.created and duplicate.run.id == run
        with pytest.raises(Conflict, match="admission is full"):
            await engine.start(snapshot=snapshot(tenant, agent, uuid4()), input=InputContent("overload"),
                source=SourceIdentity("session", uuid4(), "other"))
        await engine.input(tenant_id=tenant, run_id=run, input=InputContent("Monday"),
            source=SourceIdentity("session", identity.owner_id, "answer"), waiting_reference=waiting.waiting_reference)
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 2
        assert any("Monday" in part.value for message in model.requests[1].messages for part in message.content)
        assert any("schedule" in part.value for message in model.requests[1].messages for part in message.content)
    finally:
        await engine.close()


async def test_input_arriving_during_model_prevents_stale_completion(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, release = asyncio.Event(), asyncio.Event()
    async def reply(request):
        if len(model.requests) == 1:
            entered.set()
            await release.wait()
        return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    model = Model(reply)
    engine = runtime(test_database, model)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("first"), source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(entered.wait(), 2)
        await engine.input(tenant_id=tenant, run_id=run, input=InputContent("new requirement"), source=SourceIdentity("session", uuid4(), "next"))
        release.set()
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 2
        assert any("new requirement" in item.value for message in model.requests[1].messages for item in message.content)
    finally:
        await engine.close()


async def test_sql_commit_retry_never_repeats_model_execution(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    original = RunService.record_model_step
    attempts = 0
    async def flaky(self, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("database unavailable")
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "record_model_step", flaky)
    model = Model()
    engine = runtime(test_database, model)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert attempts == 2 and len(model.requests) == 1
    finally:
        await engine.close()


async def test_consumer_failure_retains_result_for_explicit_settlement_retry(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    class Consumer:
        calls = 0
        async def record_outcome(self, tx, *, run, outcome):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("consumer failed")
    model, consumer = Model(), Consumer()
    engine = runtime(test_database, model, consumer=consumer)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        async with asyncio.timeout(3):
            while run not in engine.dispatcher.failures:
                await asyncio.sleep(0.01)
        assert (await status(test_database, tenant, run)).status == "Running"
        await engine.retry_settlement(tenant_id=tenant, run_id=run)
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 1 and consumer.calls == 2
    finally:
        await engine.close()


async def test_postcommit_enqueue_failure_interrupts_before_releasing_capacity(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    engine = runtime(test_database)
    await engine.startup()
    def fail_wake(key):
        raise OverflowError("queue failed")
    monkeypatch.setattr(engine.dispatcher, "wake", fail_wake)
    try:
        with pytest.raises(OverflowError):
            await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        assert (await status(test_database, tenant, run)).status == "Interrupted"
        assert engine.dispatcher.admitted == 0
    finally:
        await engine.close()


async def test_close_cancels_model_and_commits_interrupted_before_release(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def forever(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    model = Model(forever)
    engine = runtime(test_database, model)
    await engine.startup()
    await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
    await asyncio.wait_for(entered.wait(), 2)
    await engine.close()
    assert stopped.is_set() and (await status(test_database, tenant, run)).status == "Interrupted"
    assert engine.dispatcher.admitted == engine.dispatcher.active == 0


async def test_startup_interrupts_old_family_without_model_replay(test_database, transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    model = Model()
    engine = runtime(test_database, model)
    await engine.startup()
    try:
        assert (await status(test_database, tenant, main.id)).status == "Interrupted"
        assert (await status(test_database, tenant, child.id)).status == "Interrupted"
        assert model.requests == [] and len(model.released) == 2
    finally:
        await engine.close()
    with pytest.raises(Conflict):
        await engine.input(tenant_id=tenant, run_id=main.id, input=InputContent("no restart"), source=SourceIdentity("session", uuid4(), "x"))


async def test_task_child_need_input_resume_and_result_return_use_same_child(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    main = uuid4()
    child_id = None
    per_run = {}
    async def reply(request):
        count = per_run[request.run_id] = per_run.get(request.run_id, 0) + 1
        if request.run_id == main and count == 1:
            calls = (ModelToolCall("delegate", "task", "{}"),)
        elif request.run_id == main and count == 2:
            await wait_status(test_database, tenant, child_id, "Waiting")
            calls = (ModelToolCall("resume", "task", "{}"),)
        elif request.run_id != main and count == 1:
            calls = (ModelToolCall("ask", "need_input", "{}"),)
        else:
            if request.run_id == main:
                await wait_status(test_database, tenant, child_id, "Completed")
            calls = ()
        return ModelStepResult("child answer" if request.run_id != main else "final comparison", calls,
            "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    async def execute(snap, step_id, available, calls):
        nonlocal child_id
        call = calls[0]
        if call.call_id == "delegate":
            child_id = await engine.delegate(tenant_id=tenant, parent_run_id=main,
                step_id=step_id, call_id=call.call_id, work="research")
            content = '{"accepted":true}'
        elif call.call_id == "resume":
            waiting = await status(test_database, tenant, child_id)
            await engine.resume(tenant_id=tenant, parent_run_id=main, child_run_id=child_id,
                step_id=step_id, call_id=call.call_id, waiting_reference=waiting.waiting_reference, answer="Monday")
            content = '{"resumed":true}'
        else:
            content = '{"need_input":true,"question":"Which day?"}'
        return ToolBatchOutcome((ToolResult(call.call_id, "success", content),), available)
    owners = []
    class Consumer:
        async def record_outcome(self, tx, *, run, outcome):
            owners.append(run.id)
    model = Model(reply)
    engine = runtime(test_database, model, Tools(execute), slots=2, consumer=Consumer())
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, main), "task", "need_input"),
            input=InputContent("research and compare"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, main, "Completed")
        assert child_id is not None and per_run[child_id] == 2
        assert (await status(test_database, tenant, child_id)).status == "Completed"
        kinds, after, offset = [], 0, 0
        while True:
            fragment = await engine.inspect_fragment(tenant_id=tenant, parent_run_id=main, child_run_id=child_id,
                after_sequence=after, content_offset=offset)
            if fragment is None:
                break
            kinds.append(fragment.kind)
            after, offset = fragment.next_after_sequence, fragment.next_offset or 0
        assert "waiting" in kinds
        assert any("Monday" in content.value for request in model.requests if request.run_id == child_id
            for message in request.messages for content in message.content)
        assert owners == [main]
    finally:
        await engine.close()


async def test_tool_commit_retry_does_not_repeat_external_tool(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    attempts = 0
    original = RunService.record_tool_result
    async def flaky(self, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("transient write failure")
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "record_tool_result", flaky)
    async def reply(request):
        calls = (ModelToolCall("call", "write_file", "{}"),) if len(model.requests) == 1 else ()
        return ModelStepResult("done", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    model, tools = Model(reply), Tools()
    engine = runtime(test_database, model, tools)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "write_file"), input=InputContent("write"),
            source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert attempts == 2 and len(tools.calls) == 1 and len(model.requests) == 2
    finally:
        await engine.close()


async def test_compacted_observed_base_survives_waiting_cache_and_projection_deletion(test_database, transaction_factory):
    from sqlalchemy import delete

    from app.modules.context.models import ContextProjectionRecord

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        count = len(model.requests)
        calls = ((ModelToolCall(f"read-{count}", "read_file", "{}"),) if count <= 2 else
            (ModelToolCall("ask", "need_input", "{}"),) if count == 3 else ())
        return ModelStepResult("done", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    async def execute(snap, step_id, available, calls):
        import json

        call = calls[0]
        content = ('{"need_input":true,"question":"Proceed?"}' if call.name == "need_input"
            else json.dumps({"content": "x" * 5000}))
        return ToolBatchOutcome((ToolResult(call.call_id, "success", content),), available)
    model = Model(reply)
    engine = runtime(test_database, model, Tools(execute))
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "read_file", "need_input"),
            input=InputContent("read two reports"), source=SourceIdentity("session", uuid4(), "q"))
        waiting = await wait_status(test_database, tenant, run, "Waiting")
        async with transaction(test_database.sessions) as tx:
            base = await RunService(tx).latest_fact(tenant_id=tenant, run_id=run, kind="context_base")
            assert base is not None
            await tx.session.execute(delete(ContextProjectionRecord).where(ContextProjectionRecord.run_id == run))
        await engine.input(tenant_id=tenant, run_id=run, input=InputContent("proceed"),
            source=SourceIdentity("session", uuid4(), "answer"), waiting_reference=waiting.waiting_reference)
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 4
        assert any("Earlier Tool output omitted" in content.value for message in model.requests[-1].messages for content in message.content)
    finally:
        await engine.close()


async def test_summary_generation_and_primary_call_use_separate_quanta(test_database, transaction_factory):
    from app.modules.context.public import ContextSummary

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    summary_calls = 0
    class Summary:
        async def summarize(self, **kwargs):
            nonlocal summary_calls
            summary_calls += 1
            return ContextSummary("preserved task", "", "read prior material", "", "", "continue", "")
    async def reply(request):
        calls = (ModelToolCall(f"call-{len(model.requests)}", "read_file", "{}"),) if len(model.requests) < 9 else ()
        return ModelStepResult("progress", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    model = Model(reply)
    snap = with_tools(snapshot(tenant, agent, run), "read_file")
    snap = replace(snap, model=replace(snap.model,
        policy=replace(snap.model.policy, context_limit=4000, output_limit=500),
        profile=replace(snap.model.profile, context_limit=4000, output_limit=500)))
    engine = runtime(test_database, model, summarizer_factory=lambda captured: Summary())
    quantum_counts = []
    original = engine.quantum
    async def observed(key):
        before = len(model.requests) + summary_calls
        result = await original(key)
        quantum_counts.append(len(model.requests) + summary_calls - before)
        return result
    engine.dispatcher._quantum = observed
    await engine.startup()
    try:
        await engine.start(snapshot=snap, input=InputContent("inspect all reports"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert summary_calls > 0 and max(quantum_counts) == 1
    finally:
        await engine.close()


async def test_task_direct_dispatch_without_originating_call_is_denied(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(request):
        entered.set()
        await release.wait()
        return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    engine = runtime(test_database, Model(blocked))
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "task"), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(InvalidInput):
            await engine.delegate(tenant_id=tenant, parent_run_id=run, step_id="invented", call_id="invented", work="unauthorized")
        assert engine.dispatcher.admitted == 1
    finally:
        release.set()
        await engine.close()


async def test_cancelled_close_waits_for_interruption_and_housekeeping(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, cleaning, finish_cleaning = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()
    class CleaningModel(Model):
        async def release_continuation(self, **values):
            cleaning.set()
            await finish_cleaning.wait()
            await super().release_continuation(**values)
    engine = runtime(test_database, CleaningModel(blocked))
    await engine.startup()
    await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
    await asyncio.wait_for(entered.wait(), 2)
    closing = asyncio.create_task(engine.close())
    await asyncio.wait_for(cleaning.wait(), 2)
    closing.cancel()
    await asyncio.sleep(0.01)
    assert not closing.done()
    finish_cleaning.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert (await status(test_database, tenant, run)).status == "Interrupted"
    assert engine.dispatcher.admitted == engine.dispatcher.active == 0


async def test_input_postcommit_hint_after_cancel_does_not_wake_released_run(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, committed_input, release_hint = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()
    engine = runtime(test_database, Model(blocked))
    original = engine._apply
    async def delayed(changed):
        if changed.run.status == "Running":
            committed_input.set()
            await release_hint.wait()
        await original(changed)
    engine._apply = delayed
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(entered.wait(), 2)
        submitting = asyncio.create_task(engine.input(tenant_id=tenant, run_id=run, input=InputContent("new"),
            source=SourceIdentity("session", uuid4(), "next")))
        await asyncio.wait_for(committed_input.wait(), 2)
        await engine.cancel(tenant_id=tenant, run_id=run)
        release_hint.set()
        assert (await submitting).changed
        assert (await status(test_database, tenant, run)).status == "Cancelled"
        assert engine.dispatcher.admitted == 0
    finally:
        release_hint.set()
        await engine.close()


async def test_continuation_cleanup_failure_cannot_reverse_completed_outcome(test_database, transaction_factory, caplog):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    class BrokenCleanup(Model):
        async def release_continuation(self, **values):
            raise RuntimeError("private cleanup details")
    engine = runtime(test_database, BrokenCleanup())
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        async with asyncio.timeout(2):
            while engine.dispatcher.active:
                await asyncio.sleep(0.01)
        assert engine.dispatcher.admitted == 0 and engine.dispatcher.failures == {}
        assert "RuntimeError" in caplog.text and "private cleanup details" not in caplog.text
    finally:
        await engine.close()


async def test_late_cancelled_model_write_finishes_before_terminal_cleanup(test_database, transaction_factory):
    from sqlalchemy import Column, MetaData, Table, Uuid, delete, func, insert, select

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    table = Table("fixture_late_continuation", MetaData(), Column("run_id", Uuid, primary_key=True), schema=test_database.schema)
    async with test_database.engine.begin() as connection:
        await connection.run_sync(table.create)
    entered, cancelling, allow_late_write, cleaned = (asyncio.Event() for _ in range(4))
    class LateModel(Model):
        async def execute_step(self, policy, request, *, on_event=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await allow_late_write.wait()
                async with transaction(test_database.sessions) as tx:
                    await tx.session.execute(insert(table).values(run_id=run))
                return ModelStepResult("late", (), "stop", ModelUsage(), request.step_id, False)
        async def release_continuation(self, **values):
            async with transaction(test_database.sessions) as tx:
                await tx.session.execute(delete(table).where(table.c.run_id == values["run_id"]))
            cleaned.set()
    engine = runtime(test_database, LateModel())
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(entered.wait(), 2)
        cancelling_run = asyncio.create_task(engine.cancel(tenant_id=tenant, run_id=run))
        await asyncio.wait_for(cancelling.wait(), 2)
        assert not cleaned.is_set() and not cancelling_run.done()
        allow_late_write.set()
        await asyncio.wait_for(cancelling_run, 3)
        async with transaction(test_database.sessions) as tx:
            assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
        assert engine.dispatcher.failures == {} and run not in engine._pending
    finally:
        allow_late_write.set()
        await engine.close()


async def test_interleaved_stream_events_retain_exact_run_scope(test_database, transaction_factory):
    from app.modules.model.public import ModelStreamEvent

    tenant, agent = await seed(transaction_factory)
    runs = (uuid4(), uuid4())
    both = asyncio.Event()
    started = set()
    observed = []
    class StreamingModel(Model):
        async def execute_step(self, policy, request, *, on_event=None):
            started.add(request.run_id)
            if len(started) == 2:
                both.set()
            await both.wait()
            for index in range(2):
                await on_event(ModelStreamEvent("text", str(request.run_id), index))
                await asyncio.sleep(0)
            return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    async def observer(key, event):
        observed.append((key, event))
    engine = runtime(test_database, StreamingModel(), observer=observer, slots=2)
    await engine.startup()
    try:
        for run in runs:
            await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), str(run)))
        await asyncio.gather(*(wait_status(test_database, tenant, run, "Completed") for run in runs))
        assert len(observed) == 4
        assert all(key.tenant_id == tenant and key.agent_id == agent and str(key.run_id) == event.text for key, event in observed)
    finally:
        await engine.close()


async def test_failed_quantum_retains_failure_settlement_when_consumer_rolls_back(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    count = 0
    async def broken(request):
        nonlocal count
        count += 1
        raise RuntimeError("model adapter defect")
    class Consumer:
        attempts = 0
        async def record_outcome(self, tx, *, run, outcome):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("consumer unavailable")
    consumer = Consumer()
    engine = runtime(test_database, Model(broken), consumer=consumer)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        async with asyncio.timeout(3):
            while run not in engine.dispatcher.failures:
                await asyncio.sleep(0.01)
        assert (await status(test_database, tenant, run)).status == "Running"
        await engine.retry_settlement(tenant_id=tenant, run_id=run)
        await wait_status(test_database, tenant, run, "Failed")
        assert count == 1 and consumer.attempts == 2
    finally:
        await engine.close()


@pytest.mark.parametrize("failure", ["raise", "slow", "self_cancel"])
async def test_optional_stream_observer_failure_does_not_fail_model_result(test_database, transaction_factory, caplog, failure):
    from app.modules.model.public import ModelStreamEvent

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    calls = 0
    class StreamingModel(Model):
        async def execute_step(self, policy, request, *, on_event=None):
            for index in range(2):
                await on_event(ModelStreamEvent("text", "partial", index))
            return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    async def observer(key, event):
        nonlocal calls
        calls += 1
        if failure == "slow":
            await asyncio.Event().wait()
        elif failure == "self_cancel":
            raise asyncio.CancelledError
        else:
            raise RuntimeError("private observer payload")
    engine = runtime(test_database, StreamingModel(), observer=observer)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert engine.observer_failures == 1 and calls == 1
        assert "private observer payload" not in caplog.text
    finally:
        await engine.close()


async def test_run_cancellation_during_observer_still_stops_execution(test_database, transaction_factory):
    from app.modules.model.public import ModelStreamEvent

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    observing, model_stopped = asyncio.Event(), asyncio.Event()
    class StreamingModel(Model):
        async def execute_step(self, policy, request, *, on_event=None):
            try:
                await on_event(ModelStreamEvent("text", "partial"))
                return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
            finally:
                model_stopped.set()
    async def observer(key, event):
        observing.set()
        await asyncio.Event().wait()
    engine = runtime(test_database, StreamingModel(), observer=observer)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(observing.wait(), 2)
        await engine.cancel(tenant_id=tenant, run_id=run)
        assert model_stopped.is_set() and engine.observer_failures == 0
        assert (await status(test_database, tenant, run)).status == "Cancelled"
    finally:
        await engine.close()


async def test_full_admission_task_returns_tool_error_without_failing_parent(test_database, transaction_factory):
    from app.execution_dependencies.run_tools import RUN_TOOL_DEFINITIONS, run_tool_bindings
    from app.modules.tool.public import CallScope, ToolCall, ToolRegistry, ToolScheduler

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = (ModelToolCall("delegate", "task", '{"action":"delegate","work":"research"}'),) if len(model.requests) == 1 else ()
        return ModelStepResult("capacity reported", calls, "tool_calls" if calls else "stop", ModelUsage(), request.step_id, False)
    class Batches:
        async def execute(self, *, snapshot, step_id, available, calls):
            class Operations:
                async def delegate(self, call_id, work):
                    return await engine.delegate(tenant_id=tenant, parent_run_id=run, step_id=step_id, call_id=call_id, work=work)
            scope = CallScope(tenant, agent, run)
            scheduler = ToolScheduler(ToolRegistry(run_tool_bindings(scope=scope, role="main", operations=Operations())),
                max_parallel=1, timeout_seconds=5)
            results = await scheduler.execute(available,
                tuple(ToolCall(call.call_id, call.name, call.arguments_json) for call in calls), scope)
            return ToolBatchOutcome(results, available)
    model = Model(reply)
    snap = snapshot(tenant, agent, run)
    definition = next(item for item in RUN_TOOL_DEFINITIONS if item.name == "task")
    snap = replace(snap, tools=AuthorizedToolSet(tenant, agent,
        (ResolvedTool(ToolDefinition(uuid4(), tenant, definition), None),)), initial_direct_names=frozenset({"task"}))
    engine = runtime(test_database, model, Batches(), slots=1, capacity=1)
    await engine.startup()
    try:
        await engine.start(snapshot=snap, input=InputContent("research"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 2
        tool_results = [message for message in model.requests[1].messages if message.role == "tool"]
        assert len(tool_results) == 1 and tool_results[0].is_error
    finally:
        await engine.close()


async def test_reused_run_id_with_new_source_cannot_release_existing_admission(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered, finished = asyncio.Event(), asyncio.Event()
    async def blocked(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    engine = runtime(test_database, Model(blocked), slots=1, capacity=1)
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(Conflict, match="identity already"):
            await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("new source"), source=SourceIdentity("session", uuid4(), "other"))
        assert not finished.is_set() and engine.dispatcher.admitted == 1
        assert (await status(test_database, tenant, run)).status == "Running"
    finally:
        await engine.close()


async def test_task_fragment_inspection_rejects_other_parent_and_other_tenant(test_database, transaction_factory):
    from modules.run.test_lifecycle import start

    from app.infrastructure.errors import NotFound

    tenant, agent, main, child = await family(transaction_factory)
    other_main = (await start(transaction_factory, tenant, agent)).run
    engine = runtime(test_database)
    assert await engine.inspect_fragment(tenant_id=tenant, parent_run_id=main.id, child_run_id=child.id) is not None
    with pytest.raises(InvalidInput):
        await engine.inspect_fragment(tenant_id=tenant, parent_run_id=other_main.id, child_run_id=child.id)
    with pytest.raises(InvalidInput):
        await engine.inspect_fragment(tenant_id=tenant, parent_run_id=child.id, child_run_id=child.id)
    with pytest.raises(NotFound):
        await engine.inspect_fragment(tenant_id=uuid4(), parent_run_id=main.id, child_run_id=child.id)


@pytest.mark.parametrize("finish,content,calls,expected,reason", [
    ("length", "partial answer", (), "Failed", "model_finish_length"),
    ("content_filter", "partial answer", (), "Failed", "model_finish_content_filter"),
    ("refusal", "I cannot help with that request.", (), "Completed", None),
    ("refusal", "", (), "Failed", "model_refusal"),
    ("stop", "done", (), "Completed", None),
    ("tool_calls", "", (), "Failed", "model_finish_protocol"),
    ("stop", "", (ModelToolCall("invalid", "read_file", "{}"),), "Failed", "model_finish_protocol"),
])
async def test_model_finish_reason_never_misreports_truncation_or_protocol_error_as_complete(
        test_database, transaction_factory, finish, content, calls, expected, reason):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        return ModelStepResult(content, calls, finish, ModelUsage(), request.step_id, False)
    tools = Tools()
    engine = runtime(test_database, Model(reply), tools)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "read_file"), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, expected)
        async with transaction(test_database.sessions) as tx:
            history = await RunService(tx).read_history(tenant_id=tenant, run_id=run)
        assert history.entries[-2].payload.result.finish_reason == finish
        assert history.entries[-1].payload.reason == reason and tools.calls == []
    finally:
        await engine.close()


async def test_projection_hash_hit_and_all_cache_misses_reconstruct_identical_model_input(test_database, transaction_factory):
    from pydantic import TypeAdapter
    from sqlalchemy import delete, select, update

    from app.modules.context.models import ContextProjectionRecord
    from app.modules.context.public import ContextProjectionService, ContextState, ContextUnit, context_state_hash
    from app.modules.model.public import ModelContent, ModelMessage, ModelToolDefinition
    from app.runtime.scheduler import RunKey

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = (ModelToolCall("ask", "need_input", "{}"),)
        return ModelStepResult("", calls, "tool_calls", ModelUsage(), request.step_id, False)
    async def execute(snap, step_id, available, calls):
        return ToolBatchOutcome((ToolResult("ask", "success", '{"need_input":true,"question":"which day?"}'),), available)
    engine = runtime(test_database, Model(reply), Tools(execute))
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "need_input"), input=InputContent("actual source"),
            source=SourceIdentity("session", uuid4(), "q"))
        waiting = await wait_status(test_database, tenant, run, "Waiting")
        key = RunKey(tenant, agent, run)
        async with transaction(test_database.sessions) as tx:
            service = RunService(tx)
            fact = await service.latest_fact(tenant_id=tenant, run_id=run, kind="model_input")
            saved = await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run)
            assert fact.payload.context_state_hash == context_state_hash(saved)
            fragment = await service.read_history_fragment(tenant_id=tenant, run_id=run, after_sequence=fact.sequence - 1)
            assert fragment.version == 2

        async def rebuild():
            async with transaction(test_database.sessions) as tx:
                service = RunService(tx)
                reads = []
                original_read = service.read_history
                async def observed_read(**kwargs):
                    reads.append(kwargs["after_sequence"])
                    return await original_read(**kwargs)
                service.read_history = observed_read
                cache = await engine._load(service, key, tx)
                initial_cursor = cache.cursor
                await engine._advance(service, key, cache, waiting.latest_history_sequence)
                definitions = tuple(ModelToolDefinition(item.spec.name, item.spec.description, item.spec.input_schema_json)
                    for item in cache.available.visible())
                result = await cache.assembler.prepare(state=cache.state, additions=tuple(cache.additions), tools=definitions)
                return initial_cursor, result.messages, reads
        cursor, expected, reads = await rebuild()
        assert cursor == saved.through_sequence == 1
        assert reads == [saved.through_sequence]
        variants = [None, {"broken": True},
            TypeAdapter(ContextState).dump_python(ContextState((ContextUnit(1,
                (ModelMessage("user", (ModelContent("text", "invented projection content"),)),)),), 1), mode="json"),
            TypeAdapter(ContextState).dump_python(replace(saved, through_sequence=999), mode="json"),
            TypeAdapter(ContextState).dump_python(ContextState(), mode="json")]
        for invalid in variants:
            async with transaction(test_database.sessions) as tx:
                await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=saved)
                if invalid is None:
                    await tx.session.execute(delete(ContextProjectionRecord).where(ContextProjectionRecord.run_id == run))
                else:
                    await tx.session.execute(update(ContextProjectionRecord).where(ContextProjectionRecord.run_id == run).values(payload=invalid))
            cursor, restored, reads = await rebuild()
            assert cursor == 0 and restored == expected
            assert reads == [0]
            async with transaction(test_database.sessions) as tx:
                assert await tx.session.scalar(select(ContextProjectionRecord.run_id).where(ContextProjectionRecord.run_id == run)) is None
                assert (await RunService(tx).get(tenant_id=tenant, run_id=run)).status == "Waiting"
        async with transaction(test_database.sessions) as tx:
            after = await RunService(tx).read_history(tenant_id=tenant, run_id=run)
            assert after.entries[-1].payload.read_through_sequence == 1
        from app.modules.run.contracts import encode_history
        from app.modules.run.models import RunHistoryRecord

        old_input = encode_history(replace(fact.payload, context_state_hash=None))
        async with transaction(test_database.sessions) as tx:
            await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=saved)
            await tx.session.execute(update(RunHistoryRecord).where(RunHistoryRecord.run_id == run,
                RunHistoryRecord.sequence == fact.sequence).values(payload_schema_version=old_input.version, payload=old_input.payload))
        cursor, restored, reads = await rebuild()
        assert cursor == 0 and reads == [0] and restored == expected
    finally:
        await engine.close()


@pytest.mark.parametrize("invalid", ["too_many_calls", "long_call_id", "large_usage", "long_interaction_id"])
async def test_unpersistable_model_result_fails_without_retaining_an_unretryable_pending(
        test_database, transaction_factory, invalid):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = ((ModelToolCall("c" * 257, "read_file", "{}"),) if invalid == "long_call_id" else
            tuple(ModelToolCall(f"call-{index}", "read_file", "{}") for index in range(129)) if invalid == "too_many_calls" else ())
        usage = ModelUsage(input_tokens=2**63) if invalid == "large_usage" else ModelUsage()
        interaction = "i" * 257 if invalid == "long_interaction_id" else request.step_id
        return ModelStepResult("provider result", calls, "tool_calls" if calls else "stop", usage, interaction, False)
    model, tools = Model(reply), Tools()
    engine = runtime(test_database, model, tools)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "read_file"), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Failed")
        async with transaction(test_database.sessions) as tx:
            page = await RunService(tx).read_history(tenant_id=tenant, run_id=run)
        assert page.entries[-1].payload.reason == "invalid_model_result"
        assert not any(type(entry.payload).__name__ == "ModelStepPayload" for entry in page.entries)
        assert len(model.requests) == 1 and tools.calls == []
        assert run not in engine._pending and engine.dispatcher.failures == {}
    finally:
        await engine.close()


async def test_maximum_valid_model_call_batch_persists_and_executes(test_database, transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async def reply(request):
        calls = tuple(ModelToolCall(f"call-{index}".ljust(256, "x"), "read_file", "{}") for index in range(128)) if len(model.requests) == 1 else ()
        return ModelStepResult("done", calls, "tool_calls" if calls else "stop",
            ModelUsage(input_tokens=2**63 - 1), request.step_id, False)
    model, tools = Model(reply), Tools()
    engine = runtime(test_database, model, tools)
    snap = with_tools(snapshot(tenant, agent, run), "read_file")
    snap = replace(snap, model=replace(snap.model,
        policy=replace(snap.model.policy, context_limit=200000), profile=replace(snap.model.profile, context_limit=200000)))
    await engine.startup()
    try:
        await engine.start(snapshot=snap, input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
        assert len(model.requests) == 2 and len(tools.calls) == 128
        assert run not in engine._pending and engine.dispatcher.failures == {}
    finally:
        await engine.close()


@pytest.mark.parametrize("defect", ["wrong_call", "duplicate_call", "invalid_json", "oversized_content", "missing_question"])
async def test_unpersistable_tool_batch_fails_once_without_pending_replay(test_database, transaction_factory, defect):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    name = "need_input" if defect == "missing_question" else "read_file"
    async def reply(request):
        return ModelStepResult("", (ModelToolCall("call", name, "{}"),), "tool_calls", ModelUsage(), request.step_id, False)
    async def defective(snap, step_id, available, calls):
        result = ToolResult("other" if defect == "wrong_call" else "call", "success",
            '{"need_input":true}' if defect == "missing_question" else "{}")
        if defect in ("invalid_json", "oversized_content"):
            # Simulate an adapter defect that bypasses the normalized dataclass constructor.
            object.__setattr__(result, "content_json", '{"value":NaN}' if defect == "invalid_json" else '{"value":"' + "x" * 262144 + '"}')
        return ToolBatchOutcome((result, result) if defect == "duplicate_call" else (result,), available)
    model, tools = Model(reply), Tools(defective)
    engine = runtime(test_database, model, tools)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), name), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Failed")
        async with transaction(test_database.sessions) as tx:
            page = await RunService(tx).read_history(tenant_id=tenant, run_id=run)
        if defect in ("invalid_json", "oversized_content", "missing_question"):
            assert page.entries[-1].payload.reason == "invalid_tool_result"
        assert not any(type(entry.payload).__name__ == "ToolResultPayload" for entry in page.entries)
        assert len(model.requests) == len(tools.calls) == 1
        assert run not in engine._pending and engine.dispatcher.failures == {}
    finally:
        await engine.close()


async def test_start_coalesces_fifty_identical_sources_with_one_creation(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    entered, release = asyncio.Event(), asyncio.Event()
    original = RunService.start
    creations = 0
    async def held(self, **kwargs):
        nonlocal creations
        creations += 1
        entered.set()
        await release.wait()
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "start", held)
    engine = runtime(test_database, slots=1, capacity=1)
    await engine.startup()
    source = SourceIdentity("session", uuid4(), "same")
    async def submit():
        return await engine.start(snapshot=snapshot(tenant, agent, uuid4()), input=InputContent("work"), source=source)
    tasks = [asyncio.create_task(submit()) for _ in range(50)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.01)
        assert len(engine._starting) == creations == engine.dispatcher.admitted == 1
        release.set()
        results = await asyncio.gather(*tasks)
        assert sum(result.created for result in results) == 1
        assert len({result.run.id for result in results}) == 1
        assert engine._starting == {}
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.close()


async def test_distinct_starts_overlap_database_work_and_close_drains_registered_creation(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    reached, release = asyncio.Event(), asyncio.Event()
    original = RunService.start
    active = set()
    async def held(self, **kwargs):
        result = await original(self, **kwargs)
        active.add(result.run.id)
        if len(active) == 2:
            reached.set()
        await release.wait()
        return result
    monkeypatch.setattr(RunService, "start", held)
    model = Model()
    engine = runtime(test_database, model, slots=1, capacity=2)
    await engine.startup()
    async def submit():
        return await engine.start(snapshot=snapshot(tenant, agent, uuid4()), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "different"))
    tasks = [asyncio.create_task(submit()) for _ in range(2)]
    try:
        await asyncio.wait_for(reached.wait(), 3)
        closing = asyncio.create_task(engine.close())
        await asyncio.sleep(0.01)
        assert not closing.done()
        with pytest.raises(Conflict):
            await submit()
        release.set()
        results = await asyncio.gather(*tasks)
        await asyncio.wait_for(closing, 5)
        views = await asyncio.gather(*(status(test_database, tenant, result.run.id) for result in results))
        assert all(view.status == "Interrupted" for view in views)
        assert model.requests == []
        assert engine._starting == {} and engine.dispatcher.admitted == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.close()


async def test_start_follower_cancel_does_not_cancel_leader_and_scope_mismatch_cannot_join(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    entered, release = asyncio.Event(), asyncio.Event()
    original = RunService.start
    async def held(self, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "start", held)
    engine = runtime(test_database)
    await engine.startup()
    source = SourceIdentity("session", uuid4(), "query")
    async def submit(snap=None, parent=None):
        return await engine.start(snapshot=snap or snapshot(tenant, agent, uuid4()), input=InputContent("work"), source=source,
            parent_run_id=parent)
    leader = asyncio.create_task(submit())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        follower = asyncio.create_task(submit())
        await asyncio.sleep(0)
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower
        assert not leader.done()
        with pytest.raises(Conflict, match="another execution scope"):
            await submit(snapshot(tenant, uuid4(), uuid4()))
        with pytest.raises(Conflict, match="another execution scope"):
            await submit(parent=uuid4())
        release.set()
        assert (await leader).created
    finally:
        release.set()
        await asyncio.gather(leader, return_exceptions=True)
        await engine.close()


async def test_cancelled_start_leader_rolls_back_releases_its_permit_and_settles_followers(test_database, transaction_factory, monkeypatch):
    from app.infrastructure.errors import NotFound

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    entered = asyncio.Event()
    original = RunService.start
    async def held(self, **kwargs):
        result = await original(self, **kwargs)
        entered.set()
        await asyncio.Event().wait()
        return result
    monkeypatch.setattr(RunService, "start", held)
    engine = runtime(test_database)
    await engine.startup()
    source = SourceIdentity("session", uuid4(), "query")
    async def submit():
        return await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=source)
    leader = asyncio.create_task(submit())
    await asyncio.wait_for(entered.wait(), 2)
    follower = asyncio.create_task(submit())
    await asyncio.sleep(0)
    leader.cancel()
    results = await asyncio.gather(leader, follower, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert engine._starting == {} and engine.dispatcher.admitted == 0
    with pytest.raises(NotFound):
        await status(test_database, tenant, run)
    monkeypatch.setattr(RunService, "start", original)
    try:
        assert (await submit()).created
    finally:
        await engine.close()


async def test_pending_start_intake_has_a_distinct_source_bound(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    entered, release = asyncio.Event(), asyncio.Event()
    original = RunService.find_by_source
    async def held(self, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, **kwargs)
    monkeypatch.setattr(RunService, "find_by_source", held)
    engine = runtime(test_database, slots=1, capacity=1)
    await engine.startup()
    source = SourceIdentity("session", uuid4(), "q")
    async def submit(identity):
        return await engine.start(snapshot=snapshot(tenant, agent, uuid4()), input=InputContent("work"), source=identity)
    leader = asyncio.create_task(submit(source))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(Conflict, match="start intake is full"):
            await submit(SourceIdentity("session", uuid4(), "other"))
        follower = asyncio.create_task(submit(source))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(leader, follower)
        assert first.created and not second.created and first.run.id == second.run.id
    finally:
        release.set()
        await asyncio.gather(leader, return_exceptions=True)
        await engine.close()


async def test_shutdown_stops_dispatch_before_waiting_for_external_start_commit(test_database, transaction_factory, monkeypatch):
    tenant, agent = await seed(transaction_factory)
    active_run, queued_run, pending_run = uuid4(), uuid4(), uuid4()
    model_entered, model_cancelled, creation_entered, commit_allowed = (asyncio.Event() for _ in range(4))
    seen = []
    async def model_call(request):
        seen.append(request.run_id)
        if request.run_id == active_run:
            model_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                model_cancelled.set()
        return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    original = RunService.start
    async def held_creation(self, **kwargs):
        result = await original(self, **kwargs)
        if result.run.id == pending_run:
            creation_entered.set()
            await commit_allowed.wait()
        return result
    monkeypatch.setattr(RunService, "start", held_creation)
    engine = runtime(test_database, Model(model_call), slots=1, capacity=3)
    await engine.startup()
    async def submit(run):
        return await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), str(run)))
    pending = None
    try:
        await submit(active_run)
        await asyncio.wait_for(model_entered.wait(), 2)
        await submit(queued_run)
        pending = asyncio.create_task(submit(pending_run))
        await asyncio.wait_for(creation_entered.wait(), 2)
        closing = asyncio.create_task(engine.close())
        await asyncio.wait_for(model_cancelled.wait(), 2)
        assert not closing.done() and not pending.done()
        assert seen == [active_run]
        commit_allowed.set()
        await asyncio.wait_for(pending, 3)
        await asyncio.wait_for(closing, 3)
        views = await asyncio.gather(*(status(test_database, tenant, run) for run in (active_run, queued_run, pending_run)))
        assert all(view.status == "Interrupted" for view in views)
        assert seen == [active_run] and engine.dispatcher.admitted == 0
    finally:
        commit_allowed.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await engine.close()


@pytest.mark.parametrize("exit_failure", ["cancel", "exception"])
async def test_start_commit_then_exit_failure_reconciles_before_releasing_admission(
        test_database, transaction_factory, monkeypatch, exit_failure):
    from contextlib import asynccontextmanager

    import app.modules.run.engine as engine_module

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    committed, exit_gate, reconciling, reconcile_gate = (asyncio.Event() for _ in range(4))
    engine = runtime(test_database)
    await engine.startup()
    real_transaction = transaction
    calls = 0
    @asynccontextmanager
    async def controlled(sessions):
        nonlocal calls
        calls += 1
        current = calls
        async with real_transaction(sessions) as tx:
            if current == 3:
                reconciling.set()
                await reconcile_gate.wait()
            yield tx
        if current == 2:
            committed.set()
            await exit_gate.wait()
            raise RuntimeError("failure after committed creation")
    monkeypatch.setattr(engine_module, "transaction", controlled)
    leader = asyncio.create_task(engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"),
        source=SourceIdentity("session", uuid4(), "q")))
    try:
        await asyncio.wait_for(committed.wait(), 2)
        assert (await status(test_database, tenant, run)).status == "Running"
        if exit_failure == "cancel":
            leader.cancel()
        else:
            exit_gate.set()
        await asyncio.wait_for(reconciling.wait(), 2)
        assert len(engine._starting) == engine.dispatcher.admitted == 1
        if exit_failure == "cancel":
            leader.cancel()
            await asyncio.sleep(0)
            leader.cancel()
        await asyncio.sleep(0.01)
        assert not leader.done()
        reconcile_gate.set()
        with pytest.raises(asyncio.CancelledError if exit_failure == "cancel" else RuntimeError):
            await leader
        assert (await status(test_database, tenant, run)).status == "Interrupted"
        assert engine.dispatcher.admitted == 0 and engine._starting == {}
    finally:
        exit_gate.set()
        reconcile_gate.set()
        await asyncio.gather(leader, return_exceptions=True)
        monkeypatch.setattr(engine_module, "transaction", real_transaction)
        await engine.close()


async def test_failed_start_readback_retains_capacity_until_shutdown_can_reconcile(test_database, transaction_factory, monkeypatch):
    from contextlib import asynccontextmanager

    import app.modules.run.engine as engine_module

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    engine = runtime(test_database)
    await engine.startup()
    calls = 0
    @asynccontextmanager
    async def broken(sessions):
        nonlocal calls
        calls += 1
        current = calls
        if current == 3:
            raise SQLAlchemyError("reconciliation unavailable")
        async with transaction(sessions) as tx:
            yield tx
        if current == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(engine_module, "transaction", broken)
    try:
        with pytest.raises(SQLAlchemyError, match="reconciliation unavailable"):
            await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"),
                source=SourceIdentity("session", uuid4(), "q"))
        assert engine.dispatcher.admitted == 1
        assert (await status(test_database, tenant, run)).status == "Running"
    finally:
        monkeypatch.setattr(engine_module, "transaction", transaction)
        await engine.close()
    assert engine.dispatcher.admitted == 0 and (await status(test_database, tenant, run)).status == "Interrupted"


@pytest.mark.parametrize("sweep_fails", [False, True])
async def test_rolled_back_start_with_failed_readback_releases_only_after_successful_shutdown_sweep(
        test_database, transaction_factory, monkeypatch, sweep_fails):
    from contextlib import asynccontextmanager

    import app.modules.run.engine as engine_module
    from app.infrastructure.errors import NotFound

    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    engine = runtime(test_database)
    await engine.startup()
    calls = 0
    @asynccontextmanager
    async def failed_readback(sessions):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise SQLAlchemyError("readback unavailable")
        async with transaction(sessions) as tx:
            yield tx
    original_start = RunService.start
    async def rolled_back(self, **kwargs):
        await original_start(self, **kwargs)
        raise RuntimeError("creation failed before commit")
    monkeypatch.setattr(engine_module, "transaction", failed_readback)
    monkeypatch.setattr(RunService, "start", rolled_back)
    with pytest.raises(SQLAlchemyError, match="readback unavailable"):
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
    assert engine.dispatcher.admitted == 1
    with pytest.raises(NotFound):
        await status(test_database, tenant, run)
    monkeypatch.setattr(engine_module, "transaction", transaction)
    if sweep_fails:
        async def unavailable(self, **kwargs):
            raise SQLAlchemyError("sweep unavailable")
        original_sweep = RunService.interrupt_batch
        monkeypatch.setattr(RunService, "interrupt_batch", unavailable)
        with pytest.raises(SQLAlchemyError, match="sweep unavailable"):
            await engine.close()
        assert engine.dispatcher.admitted == 1 and not engine._closed
        monkeypatch.setattr(RunService, "interrupt_batch", original_sweep)
        # Explicitly retry the failed owner cleanup, without pretending the first close succeeded.
        await engine._close()
    else:
        await engine.close()
    assert engine.dispatcher.admitted == 0 and engine._closed
