import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.capability_market.test_service import seed
from modules.run.test_lifecycle import snapshot
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import transaction
from app.modules.heartbeat.models import AgentHeartbeatRecord
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.run.public import RunService
from app.modules.tool.public import AgentToolResolutionScope

BASE = datetime(2026, 9, 9, 0, tzinfo=UTC)


async def setup(test_database, config=None):
    principal, agent, _ = await seed(test_database.sessions)
    async with transaction(test_database.sessions) as tx:
        view = await HeartbeatService(tx).configure(principal, agent_id=agent.id,
            config=config or HeartbeatConfig("Check progress", 5), now=BASE)
    return principal, agent, view


async def test_normal_cycles_idempotence_and_no_restart_catchup(test_database):
    principal, _, view = await setup(test_database)
    now = BASE + timedelta(minutes=5)
    async with transaction(test_database.sessions) as tx:
        service = HeartbeatService(tx)
        due = (await service.due(now=now, not_before=BASE)).items[0]
        first = await service.accept(tenant_id=principal.tenant_id, heartbeat_id=view.id,
            now=now, not_before=BASE, source_key=due.source_key, due_at=due.due_at)
        assert first.source.kind == "heartbeat" and first.source.owner_id == first.id
        assert first.delegated_connection_ids == ()
        assert await service.accept(tenant_id=principal.tenant_id, heartbeat_id=view.id,
            now=now, not_before=BASE, source_key=due.source_key, due_at=due.due_at) == first
        assert not (await service.due(now=now + timedelta(seconds=30), not_before=now + timedelta(seconds=1))).items
        later = (await service.due(now=now + timedelta(minutes=5), not_before=now + timedelta(seconds=1))).items[0]
        assert later.source_key != due.source_key


@pytest.mark.parametrize("hour,expected", [(0, False), (1, True), (9, False), (15, False)])
async def test_timezone_and_active_hours(test_database, hour, expected):
    _, _, _ = await setup(test_database, HeartbeatConfig("Check", 60, "Asia/Shanghai", "09:00", "17:00"))
    async with transaction(test_database.sessions) as tx:
        page = await HeartbeatService(tx).due(now=BASE + timedelta(hours=hour), not_before=BASE)
        assert bool(page.items) is expected


async def test_overnight_active_window_and_configure_updates_one_record(test_database):
    principal, agent, view = await setup(test_database, HeartbeatConfig("Check", 60, "UTC", "23:00", "03:00"))
    async with transaction(test_database.sessions) as tx:
        service = HeartbeatService(tx)
        assert (await service.due(now=BASE + timedelta(hours=1), not_before=BASE)).items
        assert not (await service.due(now=BASE + timedelta(hours=4), not_before=BASE)).items
        updated = await service.configure(principal, agent_id=agent.id, config=view.config, enabled=False)
        assert updated.id == view.id and not updated.enabled
        assert not (await service.due(now=BASE + timedelta(hours=1), not_before=BASE)).items


async def test_concurrent_acceptance_and_real_run_callbacks(test_database):
    principal, agent, view = await setup(test_database)
    now = BASE + timedelta(minutes=5)
    async def accept():
        async with transaction(test_database.sessions) as tx:
            return await HeartbeatService(tx).accept(tenant_id=principal.tenant_id, heartbeat_id=view.id,
                now=now, not_before=BASE, source_key=now.isoformat(), due_at=now)
    records = await asyncio.gather(*(accept() for _ in range(4)))
    assert len({r.id for r in records}) == 1
    occurrence = records[0]
    run_id = uuid4()
    async with transaction(test_database.sessions) as tx:
        owner = HeartbeatService(tx)
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent.id, run_id=run_id,
            source=occurrence.source, input=occurrence.input, snapshot=snapshot(principal.tenant_id, agent.id, run_id), start_consumer=owner)).run
        await RunService(tx).terminate(tenant_id=principal.tenant_id, run_id=run.id, status="Failed", reason="test", consumer=owner)
    async with transaction(test_database.sessions) as tx:
        stored = await HeartbeatService(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
        assert stored.run_id == run_id and stored.result.status == "Failed"


async def test_denials_versions_and_bounds(test_database):
    principal, agent, view = await setup(test_database)
    async with transaction(test_database.sessions) as tx:
        service = HeartbeatService(tx)
        with pytest.raises(NotFound):
            await service.get(replace(principal, tenant_id=uuid4()), agent_id=agent.id)
        with pytest.raises(AccessDenied):
            await service.get(replace(principal, role="member"), agent_id=agent.id)
        with pytest.raises(AccessDenied):
            await service.configure(principal, agent_id=agent.id, config=view.config, delegated_connection_ids=(uuid4(),))
        with pytest.raises(InvalidInput):
            await service.due(now=BASE, not_before=BASE, limit=0)
        with pytest.raises(Conflict):
            await service.accept(tenant_id=principal.tenant_id, heartbeat_id=view.id,
                now=BASE, not_before=BASE, source_key="fake", due_at=BASE)
        row = await tx.session.scalar(select(AgentHeartbeatRecord).where(AgentHeartbeatRecord.id == view.id))
        row.configuration = {**row.configuration, "interval_minutes": "5"}
        await tx.session.flush()
        with pytest.raises(InvalidInput):
            await service.get(principal, agent_id=agent.id)


async def test_native_main_only_configuration_uses_own_agent(test_database):
    principal, agent, view = await setup(test_database)
    scope = AgentToolResolutionScope(principal.tenant_id, agent.id, "main")
    async with transaction(test_database.sessions) as tx:
        service = HeartbeatService(tx)
        with pytest.raises(AccessDenied):
            await service.configure_for_agent(replace(scope, role="sub"), config=view.config)
        with pytest.raises(AccessDenied):
            await service.configure_for_agent(replace(scope, selected_personal_connections=(uuid4(),)), config=view.config)
        updated = await service.configure_for_agent(scope, config=HeartbeatConfig("Own work", 10))
        assert updated.id == view.id and updated.agent_id == agent.id
        assert (await service.get_for_agent(scope)).config.instruction == "Own work"


@pytest.mark.parametrize("kwargs", [{"interval_minutes": 0}, {"interval_minutes": True},
    {"interval_minutes": 5, "timezone": "invalid/path"}, {"interval_minutes": 5, "active_start": "25:00"},
    {"interval_minutes": 5, "active_start": "+1:00"}, {"interval_minutes": 5, "active_start": " 1:00"}])
def test_invalid_configuration(kwargs):
    with pytest.raises(InvalidInput):
        HeartbeatConfig("Check", **kwargs)
