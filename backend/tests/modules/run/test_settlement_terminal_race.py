"""A produced result races a committed product cancellation under real row locks."""

import asyncio

from modules.run.test_lifecycle import family
from runtime.test_engine import Model, runtime

from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.engine import _ModelCommit
from app.modules.run.public import ModelStepPayload, RunService
from app.runtime.dispatcher import RunKey


async def test_settlement_checks_terminal_status_under_the_family_lock(
        test_database, transaction_factory, monkeypatch):
    tenant, _, main, _ = await family(transaction_factory)
    engine = runtime(test_database, Model())
    key = RunKey(tenant, main.agent_id, main.id)
    engine._pending[main.id] = _ModelCommit(ModelStepPayload("race-step", main.latest_history_sequence,
        ModelStepResult("", (ModelToolCall("call", "task", "{}"),), "tool_calls", ModelUsage(), "race-step", False)))
    read_done, continue_settlement, cancelling = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_get = RunService.get

    async def paused_get(self, **kwargs):
        view = await original_get(self, **kwargs)
        read_done.set()
        await continue_settlement.wait()
        return view

    async def cancel():
        async with transaction_factory() as tx:
            cancelling.set()
            return await RunService(tx).terminate(tenant_id=tenant, run_id=main.id,
                status="Cancelled", reason="product cancellation")

    monkeypatch.setattr(RunService, "get", paused_get)
    settlement = asyncio.create_task(engine._commit_pending(key))
    async with asyncio.timeout(5):
        await read_done.wait()
        cancellation = asyncio.create_task(cancel())
        await cancelling.wait()
        await asyncio.sleep(.03)
        continue_settlement.set()
        try:
            await settlement
            ended = await cancellation
        finally:
            continue_settlement.set()
            await asyncio.gather(settlement, cancellation, return_exceptions=True)
    assert ended.run.status == "Cancelled"
    assert main.id not in engine._pending
