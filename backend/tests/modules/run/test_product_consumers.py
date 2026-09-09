"""G006 owner callbacks share real Run transactions; no product schema is fabricated into Run."""

import asyncio
from uuid import uuid4

import pytest
from modules.run.test_lifecycle import family, seed, snapshot, step
from runtime.test_engine import Model, Tools, runtime, wait_status, with_tools
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, func, insert, select

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.transactions import transaction
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import (
    InputContent,
    ModelStepPayload,
    RunService,
    SourceIdentity,
    ToolBatchOutcome,
    WaitingPayload,
)
from app.modules.tool.public import ToolResult


async def consumer_table(database):
    table = Table("fixture_product_links", MetaData(), Column("run_id", Uuid, primary_key=True),
        Column("position", Integer), schema=database.schema)
    async with database.engine.begin() as connection:
        await connection.run_sync(table.create)
    return table


async def test_start_consumer_rolls_back_all_records_and_duplicate_does_not_repeat(test_database, transaction_factory):
    table = await consumer_table(test_database)
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    source = SourceIdentity("session", uuid4(), "input")
    class Consumer:
        reject = True
        calls = 0
        async def record_started(self, tx, *, run):
            self.calls += 1
            assert run.parent_run_id is None and run.latest_history_sequence == 1 and run.source == source
            assert (await RunService(tx).read_history(tenant_id=tenant, run_id=run.id)).entries[0].source == source
            await tx.session.execute(insert(table).values(run_id=run.id, position=1))
            if self.reject:
                raise RuntimeError("product association failed")
    consumer = Consumer()
    async def start():
        async with transaction_factory() as tx:
            return await RunService(tx).start(tenant_id=tenant, agent_id=agent, run_id=run,
                snapshot=snapshot(tenant, agent, run), source=source, input=InputContent("work"), start_consumer=consumer)
    with pytest.raises(RuntimeError):
        await start()
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await RunService(tx).get(tenant_id=tenant, run_id=run)
        assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
    consumer.reject = False
    assert (await start()).created
    assert not (await start()).created
    assert consumer.calls == 2


async def test_child_creation_and_nonhuman_waits_do_not_use_main_consumers(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    class Forbidden:
        async def record_started(self, tx, *, run):
            pytest.fail("Child creation must not use a product start consumer")
        async def record_waiting(self, tx, *, run, waiting):
            pytest.fail("This is not a new Main human question")
    forbidden = Forbidden()
    main_boundary = await step(transaction_factory, tenant, main.id)
    child_boundary = await step(transaction_factory, tenant, child.id)
    async with transaction_factory() as tx:
        service = RunService(tx)
        from app.modules.run.public import derive_child
        parent_snapshot = await service.read_snapshot(tenant_id=tenant, run_id=main.id)
        new_child = uuid4()
        assert (await service.start(tenant_id=tenant, agent_id=main.agent_id, run_id=new_child,
            snapshot=derive_child(parent_snapshot, run_id=new_child), source=SourceIdentity("task", main.id, "another"),
            input=InputContent("work"), parent_run_id=main.id, start_consumer=forbidden)).created
        assert (await service.wait(tenant_id=tenant, run_id=main.id,
            payload=WaitingPayload("step", "task-wait", "", main_boundary), waiting_consumer=forbidden)).changed
        assert (await service.wait(tenant_id=tenant, run_id=child.id,
            payload=WaitingPayload("step", "child-question", "Which file?", child_boundary), waiting_consumer=forbidden)).changed


async def test_wait_consumer_suppresses_unseen_and_duplicate_and_rolls_back_question(test_database, transaction_factory):
    table = await consumer_table(test_database)
    tenant, _, main, _ = await family(transaction_factory)
    boundary = await step(transaction_factory, tenant, main.id)
    class Consumer:
        calls = 0
        reject = True
        async def record_waiting(self, tx, *, run, waiting):
            self.calls += 1
            assert run.status == "Waiting" and run.waiting_reference == waiting.reference
            await tx.session.execute(insert(table).values(run_id=run.id, position=run.latest_history_sequence))
            if self.reject:
                raise RuntimeError("question recording failed")
    consumer = Consumer()
    question = WaitingPayload("step", "question", "Which file?", boundary)
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).wait(tenant_id=tenant, run_id=main.id, payload=question, waiting_consumer=consumer)
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.get(tenant_id=tenant, run_id=main.id)).status == "Running"
        assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
        await service.append_related(tenant_id=tenant, run_id=main.id, input=InputContent("new information"),
            source=SourceIdentity("session", uuid4(), "new"))
        assert not (await service.wait(tenant_id=tenant, run_id=main.id, payload=question, waiting_consumer=consumer)).changed
    assert consumer.calls == 1
    boundary = await step(transaction_factory, tenant, main.id, "next-step")
    consumer.reject = False
    question = WaitingPayload("next-step", "new-question", "Which file?", boundary)
    async with transaction_factory() as tx:
        service = RunService(tx)
        assert (await service.wait(tenant_id=tenant, run_id=main.id, payload=question, waiting_consumer=consumer)).changed
        assert not (await service.wait(tenant_id=tenant, run_id=main.id, payload=question, waiting_consumer=consumer)).changed
    assert consumer.calls == 2


async def test_fast_runtime_never_calls_model_before_started_link_commits(test_database, transaction_factory):
    table = await consumer_table(test_database)
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    class Consumer:
        async def record_started(self, tx, *, run):
            await tx.session.execute(insert(table).values(run_id=run.id, position=1))
    async def reply(request):
        async with transaction(test_database.sessions) as tx:
            assert await tx.session.scalar(select(table.c.position).where(table.c.run_id == request.run_id)) == 1
        return ModelStepResult("done", (), "stop", ModelUsage(), request.step_id, False)
    engine = runtime(test_database, Model(reply), start_consumer=Consumer())
    await engine.startup()
    try:
        await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"), source=SourceIdentity("session", uuid4(), "q"))
        await wait_status(test_database, tenant, run, "Completed")
    finally:
        await engine.close()


async def test_runtime_start_consumer_failure_releases_admission_without_waking_model(test_database, transaction_factory):
    table = await consumer_table(test_database)
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    class Consumer:
        async def record_started(self, tx, *, run):
            await tx.session.execute(insert(table).values(run_id=run.id, position=1))
            raise RuntimeError("product association failed")
    model = Model()
    engine = runtime(test_database, model, start_consumer=Consumer())
    await engine.startup()
    try:
        with pytest.raises(RuntimeError, match="association failed"):
            await engine.start(snapshot=snapshot(tenant, agent, run), input=InputContent("work"),
                source=SourceIdentity("session", uuid4(), "q"))
        assert model.requests == [] and engine.dispatcher.admitted == engine.dispatcher.active == 0
        async with transaction_factory() as tx:
            with pytest.raises(NotFound):
                await RunService(tx).get(tenant_id=tenant, run_id=run)
            assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
    finally:
        await engine.close()


async def test_wait_consumer_retry_only_repeats_transaction_not_tool(test_database, transaction_factory):
    table = await consumer_table(test_database)
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    class Consumer:
        calls = 0
        async def record_waiting(self, tx, *, run, waiting):
            self.calls += 1
            await tx.session.execute(insert(table).values(run_id=run.id, position=run.latest_history_sequence))
            if self.calls == 1:
                raise RuntimeError("question transaction failed")
    consumer = Consumer()
    async def reply(request):
        return ModelStepResult("", (ModelToolCall("ask", "need_input", "{}"),), "tool_calls", ModelUsage(), request.step_id, False)
    async def execute(snap, step_id, available, calls):
        return ToolBatchOutcome((ToolResult("ask", "success", '{"need_input":true,"question":"Which file?"}'),), available)
    model, tools = Model(reply), Tools(execute)
    engine = runtime(test_database, model, tools, waiting_consumer=consumer)
    await engine.startup()
    try:
        await engine.start(snapshot=with_tools(snapshot(tenant, agent, run), "need_input"), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        async with asyncio.timeout(5):
            while run not in engine.dispatcher.failures:
                await asyncio.sleep(.01)
        async with transaction(test_database.sessions) as tx:
            service = RunService(tx)
            assert (await service.get(tenant_id=tenant, run_id=run)).status == "Running"
            assert await tx.session.scalar(select(func.count()).select_from(table)) == 0
            assert not any(type(entry.payload).__name__ == "ToolResultPayload"
                for entry in (await service.read_history(tenant_id=tenant, run_id=run)).entries)
        await engine.retry_settlement(tenant_id=tenant, run_id=run)
        await wait_status(test_database, tenant, run, "Waiting")
        assert len(model.requests) == len(tools.calls) == 1 and consumer.calls == 2
    finally:
        await engine.close()


async def test_product_main_lock_blocks_terminal_mutation_and_rejects_child(transaction_factory):
    tenant, _, main, child = await family(transaction_factory)
    entered = asyncio.Event()
    async def terminate():
        async with transaction_factory() as tx:
            entered.set()
            return await RunService(tx).terminate(tenant_id=tenant, run_id=main.id, status="Cancelled", reason="stop")
    async with transaction_factory() as tx:
        service = RunService(tx)
        with pytest.raises(InvalidInput):
            await service.lock_main(tenant_id=tenant, run_id=child.id)
        with pytest.raises(NotFound):
            await service.lock_main(tenant_id=uuid4(), run_id=main.id)
        assert (await service.lock_main(tenant_id=tenant, run_id=main.id)).status == "Running"
        pending = asyncio.create_task(terminate())
        await entered.wait()
        await asyncio.sleep(.03)
        assert not pending.done()
    assert (await asyncio.wait_for(pending, 2)).run.status == "Cancelled"
    async with transaction_factory() as tx:
        assert (await RunService(tx).lock_main(tenant_id=tenant, run_id=main.id)).status == "Cancelled"


async def test_product_tool_origin_requires_actual_latest_call_and_captured_grant(transaction_factory):
    tenant, agent = await seed(transaction_factory)
    run = uuid4()
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.start(tenant_id=tenant, agent_id=agent, run_id=run,
            snapshot=with_tools(snapshot(tenant, agent, run), "send_message"), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await service.record_model_step(tenant_id=tenant, run_id=run,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        verified = await service.verify_main_tool_origin(tenant_id=tenant, run_id=run,
            step_id="step", call_id="call", tool_name="send_message")
        assert verified.id == run and verified.status == "Running"
        for call_id, tool_name in (("invented", "send_message"), ("call", "task")):
            with pytest.raises(InvalidInput):
                await service.verify_main_tool_origin(tenant_id=tenant, run_id=run,
                    step_id="step", call_id=call_id, tool_name=tool_name)
    ungranted = uuid4()
    async with transaction_factory() as tx:
        service = RunService(tx)
        await service.start(tenant_id=tenant, agent_id=agent, run_id=ungranted,
            snapshot=snapshot(tenant, agent, ungranted), input=InputContent("work"),
            source=SourceIdentity("session", uuid4(), "q"))
        await service.record_model_step(tenant_id=tenant, run_id=ungranted,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        with pytest.raises(InvalidInput, match="captured authorization"):
            await service.verify_main_tool_origin(tenant_id=tenant, run_id=ungranted,
                step_id="step", call_id="call", tool_name="send_message")
