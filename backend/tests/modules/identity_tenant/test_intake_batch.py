"""Tenant availability lookup filters only a caller's explicit bounded batch."""

from uuid import uuid4

import pytest
from sqlalchemy import event

from app.infrastructure.errors import InvalidInput
from app.modules.identity_tenant.public import IdentityService


async def test_enabled_tenant_batch_is_one_query_and_excludes_unrequested(transaction_factory, test_database):
    async with transaction_factory() as tx:
        service = IdentityService(tx)
        first = await service.create_tenant(name="First")
        second = await service.create_tenant(name="Second")
        disabled = await service.create_tenant(name="Disabled", enabled=False)
        await service.create_tenant(name="Not requested")
        statements = []
        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(test_database.engine.sync_engine, "before_cursor_execute", before)
        try:
            found = await service.filter_enabled_tenant_ids(tenant_ids=(first.id, second.id, disabled.id, uuid4()))
            assert found == frozenset({first.id, second.id}) and len(statements) == 1
            statements.clear()
            assert await service.filter_enabled_tenant_ids(tenant_ids=(first.id,) * 100) == frozenset({first.id})
            assert len(statements) == 1
            statements.clear()
            with pytest.raises(InvalidInput):
                await service.filter_enabled_tenant_ids(tenant_ids=(first.id,) * 101)
            assert statements == []
            assert await service.filter_enabled_tenant_ids(tenant_ids=()) == frozenset()
        finally:
            event.remove(test_database.engine.sync_engine, "before_cursor_execute", before)
