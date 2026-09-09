from uuid import uuid4

import pytest
from modules.a2a.test_service import accept, setup
from sqlalchemy import event

from app.infrastructure.errors import InvalidInput, NotFound
from app.modules.a2a.public import A2AService


async def test_delivery_metadata_is_bounded_tenant_scoped_and_does_not_load_bodies(transaction_factory, test_database):
    principal, source, target = await setup(transaction_factory)
    request = await accept(transaction_factory, principal, source, target)
    statements = []
    def observed(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", observed)
    try:
        async with transaction_factory() as tx:
            states = await A2AService(tx).delivery_states(tenant_id=principal.tenant_id, request_ids=(request.id,))
        assert len(states) == 1 and states[0].request_id == request.id
        assert states[0].source_delivery == "awaiting_result" and states[0].result_kind is None
        assert len(statements) == 1 and ".payload" not in statements[0] and ".delegated_connections" not in statements[0]
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", observed)
    async with transaction_factory() as tx:
        owner = A2AService(tx)
        assert await owner.delivery_states(tenant_id=principal.tenant_id, request_ids=()) == ()
        with pytest.raises(NotFound):
            await owner.delivery_states(tenant_id=uuid4(), request_ids=(request.id,))
        with pytest.raises(InvalidInput):
            await owner.delivery_states(tenant_id=principal.tenant_id, request_ids=(request.id, request.id))
        with pytest.raises(InvalidInput):
            await owner.delivery_states(tenant_id=principal.tenant_id, request_ids=tuple(uuid4() for _ in range(101)))
