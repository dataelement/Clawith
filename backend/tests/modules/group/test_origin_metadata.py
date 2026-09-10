from dataclasses import replace
from uuid import uuid4

import pytest
from modules.group.test_service import setup

from app.infrastructure.errors import InvalidInput, NotFound
from app.modules.group.public import GroupService
from app.modules.run.public import InputContent


async def test_group_origin_metadata_is_tenant_scoped_membership_filtered_and_bounded(transaction_factory):
    creator, outsider, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        groups = GroupService(tx)
        assert await groups.authorized_group_ids(creator, group_ids=(group.id,)) == frozenset({group.id})
        assert not await groups.authorized_group_ids(outsider, group_ids=(group.id,))
        assert not await groups.authorized_group_ids(replace(creator, tenant_id=uuid4()), group_ids=(group.id,))
        assert not await groups.authorized_group_ids(creator, group_ids=tuple(uuid4() for _ in range(100)))
        with pytest.raises(InvalidInput):
            await groups.authorized_group_ids(creator, group_ids=tuple(uuid4() for _ in range(101)))
        topic = await groups.resolve_conversation(creator, group_id=group.id)
        accepted = await groups.accept_input(creator, group_id=group.id, source_key="metadata",
            input=InputContent("Group data"), agent_ids=(agent,), conversation_id=topic)
        assert await groups.event_conversation(tenant_id=creator.tenant_id, group_id=group.id, event_id=accepted.event.id) == topic
        with pytest.raises(NotFound):
            await groups.event_conversation(tenant_id=creator.tenant_id, group_id=uuid4(), event_id=accepted.event.id)
        with pytest.raises(NotFound):
            await groups.event_conversation(tenant_id=uuid4(), group_id=group.id, event_id=accepted.event.id)
