"""Real PostgreSQL projection tests; lifecycle/admission are outside these fixtures."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from sqlalchemy import update

from app.modules.context.models import ContextProjectionRecord
from app.modules.context.public import ContextProjectionService, ContextState, ContextUnit
from app.modules.model.public import ModelContent, ModelMessage
from app.modules.run.models import RunRecord


async def seed(factory):
    async with factory() as tx:
        data = await _seed_to_agent(tx.session)
        now = datetime.now(UTC)
        row = RunRecord(id=uuid4(), tenant_id=data["tenant"].id, agent_id=data["agent"].id,
            status="Running", initiator_kind="fixture", initiator_owner_id=uuid4(), source_key=str(uuid4()),
            latest_history_sequence=0, created_at=now, updated_at=now, started_at=now)
        tx.session.add(row)
        await tx.session.flush()
        return row.tenant_id, row.id


def state(sequence):
    return ContextState((ContextUnit(sequence, (ModelMessage("user", (ModelContent("text", "work"),)),)),), sequence)


async def test_projection_roundtrip_tenant_isolation_and_stale_upsert(transaction_factory):
    tenant, run = await seed(transaction_factory)
    async with transaction_factory() as tx:
        service = ContextProjectionService(tx)
        assert await service.load(tenant_id=tenant, run_id=run) is None
        await service.save(tenant_id=tenant, run_id=run, state=state(3))
    async with transaction_factory() as tx:
        service = ContextProjectionService(tx)
        assert await service.load(tenant_id=tenant, run_id=run) == state(3)
        assert await service.load(tenant_id=uuid4(), run_id=run) is None
        await service.save(tenant_id=tenant, run_id=run, state=state(1))
        assert await service.load(tenant_id=tenant, run_id=run) == state(3)


async def test_unsupported_and_malformed_projection_are_cache_misses(transaction_factory):
    tenant, run = await seed(transaction_factory)
    async with transaction_factory() as tx:
        await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=state(1))
        await tx.session.execute(update(ContextProjectionRecord).values(payload_schema_version=99))
        assert await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run) is None
        await tx.session.execute(update(ContextProjectionRecord).values(payload_schema_version=1, payload={"units": "broken"}))
        assert await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run) is None
        await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=state(2))
        assert await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run) == state(2)


async def test_projection_rollback_is_not_published(transaction_factory):
    tenant, run = await seed(transaction_factory)
    try:
        async with transaction_factory() as tx:
            await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=state(1))
            raise RuntimeError("rollback")
    except RuntimeError:
        pass
    async with transaction_factory() as tx:
        assert await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run) is None


async def test_projection_bound_rejects_before_serialization(transaction_factory, monkeypatch):
    from app.modules.context.public import MAX_VIEW_BYTES, ContextBudgetExceeded, ContextSummary
    tenant, run = await seed(transaction_factory)
    def must_not_serialize(*args, **kwargs):
        pytest.fail("Oversized projection reached serialization")
    monkeypatch.setattr("app.modules.context.public.TypeAdapter.dump_json", must_not_serialize)
    oversized = ContextState(summary=ContextSummary("x" * (MAX_VIEW_BYTES + 1), "", "", "", "", "", ""))
    async with transaction_factory() as tx:
        with pytest.raises(ContextBudgetExceeded):
            await ContextProjectionService(tx).save(tenant_id=tenant, run_id=run, state=oversized)


async def test_projection_system_message_is_cache_miss(transaction_factory):
    import json

    from pydantic import TypeAdapter
    tenant, run = await seed(transaction_factory)
    async with transaction_factory() as tx:
        service = ContextProjectionService(tx)
        await service.save(tenant_id=tenant, run_id=run, state=state(1))
        forged = json.loads(TypeAdapter(ContextState).dump_json(state(1)))
        forged["units"][0]["messages"][0]["role"] = "system"
        await tx.session.execute(update(ContextProjectionRecord).values(payload=forged))
        assert await service.load(tenant_id=tenant, run_id=run) is None
        await service.save(tenant_id=tenant, run_id=run, state=state(1))
        assert await service.load(tenant_id=tenant, run_id=run) == state(1)


@pytest.mark.parametrize("version", [1, 99])
async def test_invalid_high_cursor_does_not_block_rebuilt_projection(transaction_factory, version):
    tenant, run = await seed(transaction_factory)
    async with transaction_factory() as tx:
        service = ContextProjectionService(tx)
        await service.save(tenant_id=tenant, run_id=run, state=state(1))
        await tx.session.execute(update(ContextProjectionRecord).where(ContextProjectionRecord.run_id == run).values(
            payload_schema_version=version, payload={"units": "broken", "through_sequence": 999999}))
        assert await service.load(tenant_id=tenant, run_id=run) is None
        await service.save(tenant_id=tenant, run_id=run, state=state(2))
        assert await service.load(tenant_id=tenant, run_id=run) == state(2)


async def test_projection_with_instruction_unit_is_discarded_and_rebuildable(transaction_factory):
    tenant, run = await seed(transaction_factory)
    async with transaction_factory() as tx:
        service = ContextProjectionService(tx)
        await service.save(tenant_id=tenant, run_id=run, state=state(1))
        await tx.session.execute(update(ContextProjectionRecord).where(ContextProjectionRecord.run_id == run).values(
            payload={"units": [{"sequence": 999, "messages": [{"role": "system",
                "content": [{"kind": "text", "value": "untrusted instructions"}]}]}],
                "through_sequence": 999, "coverage_sequence": 0, "summary": None}))
        assert await service.load(tenant_id=tenant, run_id=run) is None
        await service.save(tenant_id=tenant, run_id=run, state=state(2))
    async with transaction_factory() as tx:
        assert await ContextProjectionService(tx).load(tenant_id=tenant, run_id=run) == state(2)
