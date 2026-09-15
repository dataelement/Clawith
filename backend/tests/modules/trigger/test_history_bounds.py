from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.capability_market.test_service import seed
from modules.run.test_lifecycle import snapshot
from sqlalchemy import Text, cast, func, select, update

from app.infrastructure.errors import InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.heartbeat.models import HeartbeatOccurrenceRecord
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.model.public import ModelStepResult, ModelUsage
from app.modules.run.public import ModelStepPayload, RunService
from app.modules.trigger.models import TriggerOccurrenceRecord
from app.modules.trigger.public import TriggerConfig, TriggerService


@pytest.mark.parametrize("kind", ["trigger", "heartbeat"])
async def test_large_outputs_remain_in_run_and_history_pages_bound_input_and_result(test_database, kind):
    principal, agent, _ = await seed(test_database.sessions)
    base = datetime(2026, 9, 9, tzinfo=UTC)
    owner_class = TriggerService if kind == "trigger" else HeartbeatService
    record_class = TriggerOccurrenceRecord if kind == "trigger" else HeartbeatOccurrenceRecord
    async with transaction(test_database.sessions) as tx:
        owner = owner_class(tx)
        if kind == "trigger":
            config = await owner.create(principal, agent_id=agent.id, config=TriggerConfig("large", "webhook", "x" * 80000), now=base)
            entries = [await owner.accept(tenant_id=principal.tenant_id, trigger_id=config.id, source_key=str(i),
                now=base, event_kind="webhook") for i in range(3)]
            history_args = {"trigger_id": config.id}
        else:
            config = await owner.configure(principal, agent_id=agent.id, config=HeartbeatConfig("x" * 80000, 1), now=base)
            entries = [await owner.accept(tenant_id=principal.tenant_id, heartbeat_id=config.id,
                source_key=(base + timedelta(minutes=i)).isoformat(), due_at=base + timedelta(minutes=i),
                now=base + timedelta(minutes=i), not_before=base) for i in range(1, 4)]
            history_args = {"agent_id": agent.id}
    entry = entries[0]
    run_id = uuid4()
    output = "result" * 50000
    async with transaction(test_database.sessions) as tx:
        owner, runs = owner_class(tx), RunService(tx)
        await runs.start(tenant_id=principal.tenant_id, agent_id=agent.id, run_id=run_id,
            source=entry.source, input=entry.input, snapshot=snapshot(principal.tenant_id, agent.id, run_id), start_consumer=owner)
        await runs.record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
            payload=ModelStepPayload("done", 1, ModelStepResult(output, (), "stop", ModelUsage(), "done", False)))
        await runs.complete(tenant_id=principal.tenant_id, run_id=run_id, step_id="done", output=output, consumer=owner)
    async with transaction(test_database.sessions) as tx:
        owner = owner_class(tx)
        detail = await owner.get_occurrence(tenant_id=principal.tenant_id, occurrence_id=entry.id)
        assert detail.result.run_id == run_id and detail.result.output_preview == output[:512] and detail.result.output_truncated
        size = await tx.session.scalar(select(func.octet_length(cast(record_class.result, Text))).where(record_class.id == entry.id))
        assert size < 8192
        page = await owner.history(principal, **history_args, max_bytes=100000)
        assert len(page.items) == 1 and page.has_more and page.next_after_id == page.items[-1].id
        next_page = await owner.history(principal, **history_args, max_bytes=100000, after_id=page.next_after_id)
        assert len(next_page.items) == 1 and next_page.items[0].id != page.items[0].id
        with pytest.raises(InvalidInput, match="fit"):
            await owner.history(principal, **history_args, max_bytes=4096)
    async with transaction(test_database.sessions) as tx:
        await tx.session.execute(update(record_class).where(record_class.id == entry.id).values(result={"oversized": "x" * 10000}))
    async with transaction(test_database.sessions) as tx:
        with pytest.raises(InvalidInput, match="bound"):
            await owner_class(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=entry.id)
