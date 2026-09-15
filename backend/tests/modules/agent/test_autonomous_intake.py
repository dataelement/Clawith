"""Execution lookup is Tenant-scoped without borrowing administrator authority."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from sqlalchemy import event

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.agent.models import AgentRecord
from app.modules.agent.public import MAX_PERMISSION_AGENT_SCAN, AgentService
from app.modules.identity_tenant.public import TenantPrincipal


async def test_autonomous_agent_read_rejects_wrong_tenant_disabled_and_archived(transaction_factory, monkeypatch):
    async with transaction_factory() as tx:
        seed = await _seed_to_agent(tx.session)
        agent, tenant = seed["agent"], seed["tenant"].id
        def no_admin(*args, **kwargs):
            pytest.fail("Autonomous read must not fabricate administrator authority")
        monkeypatch.setattr("app.modules.agent.public.require_admin", no_admin)
        service = AgentService(tx)
        assert (await service.get_for_agent_execution(tenant_id=tenant, agent_id=agent.id)).id == agent.id
        with pytest.raises(NotFound):
            await service.get_for_agent_execution(tenant_id=uuid4(), agent_id=agent.id)
        agent.enabled = False
        await tx.session.flush()
        with pytest.raises(NotFound):
            await service.get_for_agent_execution(tenant_id=tenant, agent_id=agent.id)
        agent.enabled, agent.archived_at = True, datetime.now(UTC)
        await tx.session.flush()
        with pytest.raises(NotFound):
            await service.get_for_agent_execution(tenant_id=tenant, agent_id=agent.id)


async def test_execution_batch_one_query_and_bounds_before_io(transaction_factory, test_database):
    async with transaction_factory() as tx:
        seed = await _seed_to_agent(tx.session)
        original = seed["agent"]
        agents = [original]
        for index in range(20):
            row = AgentRecord(id=uuid4(), tenant_id=original.tenant_id, model_id=original.model_id,
                name=f"Agent {index}", soul="Agent", timezone="UTC", enabled=True,
                created_by_membership_id=original.created_by_membership_id,
                created_at=original.created_at, updated_at=original.updated_at)
            agents.append(row)
            tx.session.add(row)
        await tx.session.flush()
        ids = tuple(row.id for row in agents)
        principal = TenantPrincipal(seed["account"].id, seed["membership"].id, original.tenant_id, "member", frozenset(ids))
        statements = []
        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(test_database.engine.sync_engine, "before_cursor_execute", before)
        try:
            await AgentService(tx).require_execution_ids(principal, agent_ids=ids)
            assert len(statements) == 1
            statements.clear()
            await AgentService(tx).require_execution_ids(principal, agent_ids=(original.id,) * MAX_PERMISSION_AGENT_SCAN)
            assert len(statements) == 1
            statements.clear()
            with pytest.raises(InvalidInput):
                await AgentService(tx).require_execution_ids(principal, agent_ids=(original.id,) * (MAX_PERMISSION_AGENT_SCAN + 1))
            with pytest.raises(AccessDenied):
                await AgentService(tx).require_execution_ids(replace(principal, allowed_agent_ids=frozenset()), agent_ids=ids)
            assert statements == []
            with pytest.raises(NotFound):
                await AgentService(tx).require_execution_ids(replace(principal, tenant_id=uuid4()), agent_ids=ids)
        finally:
            event.remove(test_database.engine.sync_engine, "before_cursor_execute", before)
        original.enabled = False
        await tx.session.flush()
        with pytest.raises(NotFound):
            await AgentService(tx).require_execution_ids(principal, agent_ids=ids)
